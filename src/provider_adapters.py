"""Read-only adapters. Credentials are reduced to booleans here and go no further."""
import http.client
import json
from urllib.parse import urlsplit

from src.provider_catalog import (AUTH_KEY, AUTH_NONE, AUTH_OAUTH, AUTH_PROCESS, Entry,
                                  canonical_url, is_loopback, redact_url)

# Hermes native providers whose public API is a documented OpenAI-compatible base URL.
# Anything not listed stays native-only: shown, never mirrored.
KNOWN_OPENAI_COMPATIBLE = {
    'openrouter': 'https://openrouter.ai/api/v1',
    'openai': 'https://api.openai.com/v1',
    'openai-api': 'https://api.openai.com/v1',
    'mistralai': 'https://api.mistral.ai/v1',
    'xai': 'https://api.x.ai/v1',
    'x-ai': 'https://api.x.ai/v1',
    'fireworks': 'https://api.fireworks.ai/inference/v1',
    'deepinfra': 'https://api.deepinfra.com/v1/openai',
    'nvidia': 'https://integrate.api.nvidia.com/v1',
    'opencode-zen': 'https://opencode.ai/zen/v1',        # from the Agent's own provider registry (hermes_cli/auth.py)
    'opencode-go': 'https://opencode.ai/zen/go/v1',
}
_MAX_BODY = 262144
_MAX_CONFIG = 8 * 1024 * 1024


def read_hermes_provider_urls(config_path):
    """Read-only: {provider id -> base_url} for custom and self-hosted providers in Hermes's config.yaml.

    Hermes's /api/providers omits base_url for custom providers. Only `base_url` strings are kept;
    every other field of the config (API keys, tokens, headers) is dropped here and never returned.
    """
    import yaml
    try:
        path = __import__('pathlib').Path(config_path)
        if not path.is_file() or path.stat().st_size > _MAX_CONFIG:
            return {}
        document = yaml.safe_load(path.read_text(encoding='utf-8'))
    except (OSError, ValueError, yaml.YAMLError):
        return {}
    urls = {}
    if not isinstance(document, dict):
        return urls
    providers = document.get('providers')
    for key, record in (providers.items() if isinstance(providers, dict) else []):
        for field in ('base_url', 'api', 'url'):
            value = record.get(field) if isinstance(record, dict) else None
            if isinstance(value, str) and value.strip():
                urls[str(key)] = value.strip()
                break
    custom = document.get('custom_providers')
    for record in (custom if isinstance(custom, list) else []):
        if isinstance(record, dict) and isinstance(record.get('name'), str) and isinstance(record.get('base_url'), str):
            urls.setdefault('custom:' + record['name'].strip().lower().replace(' ', '-'), record['base_url'].strip())
    return urls


def model_ids(raw):
    """Model ids from a JSON string or list of ids / {'id': ...} objects; order kept, de-duplicated."""
    if isinstance(raw, str):
        try:
            raw = json.loads(raw) if raw.strip() else []
        except ValueError:
            return ()
    if not isinstance(raw, list):
        return ()
    seen, out = set(), []
    for item in raw:
        value = item.get('id') if isinstance(item, dict) else item
        if isinstance(value, str) and value.strip() and value not in seen and len(value) <= 256:
            seen.add(value)
            out.append(value)
    return tuple(out)


def odysseus_entries(rows):
    """rows: ModelEndpoint-like objects. Never touches row.api_key beyond truthiness."""
    entries = []
    for row in rows:
        canonical = canonical_url(row.base_url)
        hidden = set(model_ids(getattr(row, 'hidden_models', None)))
        models = tuple(m for m in model_ids(getattr(row, 'cached_models', None)) + model_ids(getattr(row, 'pinned_models', None))
                       if m not in hidden)
        models = tuple(dict.fromkeys(models))
        has_key = bool(getattr(row, 'api_key', None))
        if getattr(row, 'provider_auth_id', None):
            auth = AUTH_OAUTH          # subscription-style login: not portable
        elif has_key:
            auth = AUTH_KEY
        elif canonical and is_loopback(canonical):
            auth = AUTH_NONE
        else:
            auth = AUTH_KEY            # remote endpoint without a key: will need one
        entries.append(Entry(
            source='odysseus', native_id=str(row.id), name=str(row.name or row.id)[:120],
            base_url=redact_url(row.base_url), canonical=canonical, models=models, auth=auth,
            has_credential=has_key or bool(getattr(row, 'provider_auth_id', None)),
            enabled=bool(getattr(row, 'is_enabled', True)),
            self_hosted=bool(canonical and is_loopback(canonical)),
        ))
    return entries


def hermes_entries(providers_payload, config_urls=None):
    """providers_payload: the JSON of Hermes GET /api/providers (which carries has_key, never keys).

    Only providers the user actually set up are returned: a credential, an OAuth login,
    or a self-hosted endpoint with a base URL.
    """
    entries = []
    for item in (providers_payload or {}).get('providers', []):
        if not isinstance(item, dict) or not isinstance(item.get('id'), str):
            continue
        pid = item['id']
        has_key = bool(item.get('has_key'))
        oauth = bool(item.get('is_oauth'))
        base = item.get('base_url') if isinstance(item.get('base_url'), str) else None
        self_hosted = bool(item.get('is_self_hosted'))
        custom = bool(item.get('is_custom'))
        if not base and (custom or self_hosted):
            base = (config_urls or {}).get(pid)
        # An OAuth provider without a stored login is only a catalog entry, not something the user set up.
        if not (has_key or custom or (self_hosted and base)):
            continue
        canonical = canonical_url(base) if base else canonical_url(KNOWN_OPENAI_COMPATIBLE.get(pid))
        if oauth:
            auth = AUTH_OAUTH
        elif 'subscription' in pid or (base or '').startswith('process://'):
            auth = AUTH_PROCESS
        elif (self_hosted or custom) and canonical and is_loopback(canonical):
            auth = AUTH_KEY if has_key else AUTH_NONE
        else:
            auth = AUTH_KEY
        entries.append(Entry(
            source='hermes', native_id=pid, name=str(item.get('display_name') or pid)[:120],
            base_url=redact_url(base or KNOWN_OPENAI_COMPATIBLE.get(pid, '')) if (base or pid in KNOWN_OPENAI_COMPATIBLE) else None,
            canonical=canonical, models=model_ids(item.get('models')), auth=auth,
            has_credential=has_key, enabled=True,
            reason='auth_error' if item.get('auth_error') else ('plugin' if item.get('is_plugin_provider') and not base and pid not in KNOWN_OPENAI_COMPATIBLE else ''),
            self_hosted=self_hosted,
        ))
    return entries


def fetch_hermes_providers(hermes_url, timeout=5):
    """Loopback GET of Hermes's own provider list. Returns (payload|None, state)."""
    parts = urlsplit(hermes_url)
    if parts.scheme != 'http' or parts.hostname != '127.0.0.1' or not parts.port:
        return None, 'unavailable'
    connection = http.client.HTTPConnection('127.0.0.1', parts.port, timeout=timeout)
    try:
        connection.request('GET', '/api/providers', headers={'Accept': 'application/json'})
        response = connection.getresponse()
        body = response.read(_MAX_BODY + 1)
        if response.status in (401, 403):
            return None, 'requires_authentication'
        if response.status != 200 or len(body) > _MAX_BODY:
            return None, 'unavailable'
        payload = json.loads(body)
        return (payload, 'ready') if isinstance(payload, dict) else (None, 'unavailable')
    except (OSError, ValueError, http.client.HTTPException):
        return None, 'offline'
    finally:
        connection.close()


def probe_local_models(canonical, timeout=3):
    """Credential-free OpenAI-compatible model listing for LOOPBACK endpoints only.

    Sends no Authorization header and follows no redirects. Returns (state, model_ids).
    state: 'online' | 'offline' | 'requires_authentication' | 'error'.
    """
    if not canonical or not is_loopback(canonical):
        return 'error', ()
    parts = urlsplit(canonical)
    connection = http.client.HTTPConnection('127.0.0.1', parts.port or 80, timeout=timeout)
    try:
        connection.request('GET', parts.path.rstrip('/') + '/models', headers={'Accept': 'application/json'})
        response = connection.getresponse()
        body = response.read(_MAX_BODY + 1)
        if response.status in (401, 403):
            return 'requires_authentication', ()
        if response.status != 200 or len(body) > _MAX_BODY:
            return 'error', ()
        data = json.loads(body)
        listing = data.get('data') if isinstance(data, dict) else data
        return 'online', model_ids(listing)
    except (OSError, http.client.HTTPException):
        return 'offline', ()
    except ValueError:
        return 'error', ()
    finally:
        connection.close()

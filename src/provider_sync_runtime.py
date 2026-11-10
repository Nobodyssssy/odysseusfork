"""Wires ProviderSync to the real Odysseus database and the real Hermes configuration."""
import hashlib
import json
import logging
import os
from pathlib import Path
import re
import subprocess

from src.hermes_component import ComponentError, atomic_json, read_config
from src.provider_adapters import fetch_hermes_providers, read_hermes_provider_urls
from src.provider_catalog import HERMES_KEY_ENV, canonical_url, is_loopback
from src.provider_sync import ProviderSync, SyncError

_APPEND_SCRIPT = Path(__file__).resolve().parents[1] / 'scripts' / 'hermes_config_append_provider.py'


def create_odysseus_endpoint(definition, session_factory=None, model_cls=None):
    """Additive insert. Never sets a key, never changes an existing row. 'present' if the URL exists."""
    if session_factory is None:
        from core.database import ModelEndpoint, SessionLocal
        session_factory, model_cls = SessionLocal, ModelEndpoint
    target = canonical_url(definition['base_url'])
    if not target:
        raise SyncError('The provider URL is not usable.')
    # Same outbound-URL validation Odysseus applies when a user adds an endpoint by hand.
    from src.url_safety import check_outbound_url
    allowed, reason = check_outbound_url(target, block_private=os.getenv('MODELENDPOINT_BLOCK_PRIVATE_IPS', 'false').lower() == 'true')
    if not allowed:
        raise SyncError(f'Odysseus rejected that endpoint URL: {reason}')
    db = session_factory()
    try:
        if any(canonical_url(row.base_url) == target for row in db.query(model_cls).all()):
            return 'present'
        db.add(model_cls(
            id='sync-' + hashlib.sha256(target.encode()).hexdigest()[:12], name=str(definition['name'])[:120],
            base_url=definition['base_url'], api_key=None, is_enabled=True,
            cached_models=json.dumps(list(definition['models'])), model_type='llm',
            endpoint_kind='local' if is_loopback(target) else 'auto'))
        db.commit()
        return 'created'
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


logger = logging.getLogger(__name__)
_KEY_SHAPE = re.compile(r'[\x21-\x7e]{8,4096}')


def read_env_value(path, names):
    """First non-empty value among `names` in a dotenv file, or None. The value is never logged or returned elsewhere."""
    try:
        path = Path(path)
        if path.stat().st_size > 1 << 20:
            return None
        lines = path.read_text(encoding='utf-8-sig', errors='replace').splitlines()
    except OSError:
        return None
    found = {}
    for line in lines:
        line = line.strip()
        if not line or line.startswith('#') or '=' not in line:
            continue
        if line.startswith('export '):
            line = line[7:].lstrip()
        name, _, value = line.partition('=')
        name = name.strip()
        if name not in names:
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in '"\'':
            value = value[1:-1]
        elif ' #' in value:
            value = value.split(' #', 1)[0].rstrip()
        found[name] = value
    return next((found[n] for n in names if found.get(n)), None)


def store_odysseus_key(definition, secret, session_factory=None, model_cls=None):
    """Put `secret` on the Odysseus endpoint for this URL (creating it if absent). Never replaces an existing key.

    Odysseus's own EncryptedText column encrypts it at rest. Errors are reported without any detail that could
    carry the value.
    """
    if session_factory is None:
        from core.database import ModelEndpoint, SessionLocal
        session_factory, model_cls = SessionLocal, ModelEndpoint
    target = canonical_url(definition['base_url'])
    if not target:
        raise SyncError('The provider URL is not usable.')
    from src.url_safety import check_outbound_url
    allowed, reason = check_outbound_url(target, block_private=os.getenv('MODELENDPOINT_BLOCK_PRIVATE_IPS', 'false').lower() == 'true')
    if not allowed:
        raise SyncError(f'Odysseus rejected that endpoint URL: {reason}')
    db = session_factory()
    try:
        existing = next((row for row in db.query(model_cls).all() if canonical_url(row.base_url) == target), None)
        if existing is not None:
            if existing.api_key:
                raise SyncError('Odysseus already has a key for this provider; it was not changed.')
            existing.api_key = secret
            outcome = 'key_set'
        else:
            db.add(model_cls(
                id='sync-' + hashlib.sha256(target.encode()).hexdigest()[:12], name=str(definition['name'])[:120],
                base_url=definition['base_url'], api_key=secret, is_enabled=True,
                cached_models=json.dumps(list(definition['models'])), model_type='llm',
                endpoint_kind='local' if is_loopback(target) else 'auto'))
            outcome = 'created'
        db.commit()
        return outcome
    except SyncError:
        db.rollback()
        raise
    except Exception:
        db.rollback()
        raise SyncError('Odysseus could not store the key; nothing was changed.') from None
    finally:
        db.close()


def make_key_copier(env_path, session_factory=None, model_cls=None):
    """The ONLY place a Hermes key is read. Returns copy(hermes_provider_id, definition) -> 'created' | 'key_set'."""
    def copy(provider_id, definition):
        names = HERMES_KEY_ENV.get(provider_id)
        if not names:
            raise SyncError('This provider cannot be copied.')
        secret = read_env_value(env_path, names)
        if not secret or not _KEY_SHAPE.fullmatch(secret):
            raise SyncError('Hermes does not hold a usable key for this provider in its .env file; nothing was changed.')
        try:
            outcome = store_odysseus_key(definition, secret, session_factory, model_cls)
        finally:
            secret = None
        logger.info('Provider key for %s copied from Hermes into Odysseus (%s)', provider_id, outcome)
        return outcome
    return copy


def make_hermes_appender(python, config_path, backup_dir):
    def append(definition):
        try:
            result = subprocess.run(
                [str(python), '-I', '-B', str(_APPEND_SCRIPT), str(config_path), str(backup_dir)],
                input=json.dumps({k: definition[k] for k in ('slug', 'name', 'base_url', 'models')}),
                capture_output=True, text=True, timeout=60, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise SyncError('Hermes configuration could not be updated; nothing was changed.') from exc
        if result.returncode == 0:
            return 'added'
        if result.returncode == 3:
            return 'present'
        raise SyncError('Hermes refused the change: ' + (result.stderr.strip().splitlines() or ['unknown reason'])[-1][:200])
    return append


class _Unavailable:
    def __call__(self, *args, **kwargs):
        raise SyncError('Hermes is not configured for provider sharing.')


def create_provider_sync(environment=None, invalidate=lambda: None):
    """Build the live service, or None when Hermes mode is not configured."""
    from src.runtime_paths import get_hermes_component_root
    root = get_hermes_component_root(environment)
    try:
        config = read_config(Path(root) / 'managed' / 'component.json') if root else None
    except ComponentError:
        config = None
    if not config or not config.get('enabled'):
        return None
    state_path = Path(root) / 'managed' / 'provider-sync.json'
    backups = Path(root) / 'managed' / 'provider-sync-backups'

    def load():
        try:
            return json.loads(state_path.read_text(encoding='utf-8')) if state_path.exists() else {}
        except (OSError, ValueError):
            return {}

    def save(data):
        if state_path.exists():
            try:
                json.loads(state_path.read_text(encoding='utf-8'))
            except (OSError, ValueError):
                state_path.replace(state_path.with_suffix('.corrupt'))   # keep what we could not read
        atomic_json(state_path, data)

    def rows():
        from core.database import ModelEndpoint, SessionLocal
        db = SessionLocal()
        try:
            return list(db.query(ModelEndpoint).all())
        finally:
            db.close()

    return ProviderSync(
        load, save, rows, lambda: fetch_hermes_providers(config['url']), create_odysseus_endpoint,
        make_hermes_appender(config['python'], Path(config['home']) / 'config.yaml', backups), on_odysseus_change=invalidate,
        hermes_urls=lambda: read_hermes_provider_urls(Path(config['home']) / 'config.yaml'),
        copy_key=make_key_copier(Path(config['home']) / '.env'))

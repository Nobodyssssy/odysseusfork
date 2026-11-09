"""Wires ProviderSync to the real Odysseus database and the real Hermes configuration."""
import hashlib
import json
import os
from pathlib import Path
import subprocess

from src.hermes_component import ComponentError, atomic_json, read_config
from src.provider_adapters import fetch_hermes_providers, read_hermes_provider_urls
from src.provider_catalog import canonical_url, is_loopback
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
        hermes_urls=lambda: read_hermes_provider_urls(Path(config['home']) / 'config.yaml'))

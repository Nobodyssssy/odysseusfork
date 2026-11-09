"""Run the opt-in two-mode prototype without changing either live installation."""
import argparse
import hashlib
import http.client
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import threading
import time
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
ENV_MARKER = '# Isolated mode prototype; configuration is supplied by launch-mode-dev.py.\n'


def _private_directory(path):
    path.mkdir(parents=True)
    if os.name == 'nt':
        who = subprocess.check_output(['whoami'], text=True).strip()
        subprocess.run(['icacls', str(path), '/inheritance:r', '/grant:r', who + ':(OI)(CI)F', '*S-1-5-18:(OI)(CI)F'], check=True, capture_output=True)
    else:
        path.chmod(0o700)


def _validate_state_paths(state):
    for relative in ('odysseus', 'hermes', 'hermes/webui', 'extensions', 'browser-profile'):
        path = state / relative
        if not path.resolve().is_relative_to(state):
            raise RuntimeError('Development state redirects outside its private directory')
    for relative in ('odysseus', 'hermes/webui'):
        for path in (state / relative).rglob('*'):
            if not path.resolve().is_relative_to(state):
                raise RuntimeError('A development state file redirects outside its private directory')


def prepare_state(root, snapshot):
    root, snapshot = Path(root).resolve(), Path(snapshot).resolve()
    state = root / '.mode-dev'
    env_file = root / '.env'
    if env_file.resolve() != env_file:
        raise RuntimeError('The development .env must not redirect outside its worktree')
    if env_file.exists() and env_file.read_text(encoding='utf-8-sig') != ENV_MARKER:
        raise RuntimeError('Refusing to replace an existing .env; use the dedicated integration worktree')
    if state.is_symlink() or state.resolve() != state:
        raise RuntimeError('Development state must not be a link or junction')
    marker = state / 'isolation.json'
    if state.exists():
        if not marker.is_file() or json.loads(marker.read_text()).get('version') != 1:
            raise RuntimeError('Refusing to overwrite unmarked development state')
        _validate_state_paths(state)
        if not env_file.exists():
            env_file.write_text(ENV_MARKER, encoding='utf-8')
        return state
    manifest = json.loads((snapshot / 'manifest.json').read_text(encoding='utf-8'))
    for entry in manifest['entries']:
        path = (snapshot / entry['relative_path']).resolve()
        if not path.is_relative_to(snapshot):
            raise RuntimeError('Snapshot manifest escapes its directory')
        with path.open('rb') as handle:
            if hashlib.file_digest(handle, 'sha256').hexdigest() != entry['sha256']:
                raise RuntimeError('Snapshot verification failed: ' + entry['relative_path'])
    _private_directory(state)
    shutil.copytree(snapshot / 'odysseus/data', state / 'odysseus')
    shutil.copytree(snapshot / 'hermes', state / 'hermes')
    _validate_state_paths(state)
    # This blocks python-dotenv from discovering the production .env in a parent.
    env_file.write_text(ENV_MARKER, encoding='utf-8')
    marker.write_text(json.dumps({'version': 1, 'snapshot': str(snapshot)}, indent=2), encoding='utf-8')
    return state


def open_browser_when_ready(edge, profile, url, attempts=60):
    port = urlsplit(url).port
    for _ in range(attempts):
        connection = http.client.HTTPConnection('127.0.0.1', port, timeout=2)
        try:
            connection.request('GET', '/api/health')
            response = connection.getresponse()
            health = json.loads(response.read(65536))
            if response.status == 200 and health.get('status') == 'healthy':
                subprocess.Popen([str(edge), '--user-data-dir=' + str(profile), '--no-first-run', url], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                return
        except (OSError, ValueError, http.client.HTTPException):
            pass
        finally:
            connection.close()
        time.sleep(1)
    print('Browser not opened: development Odysseus is not healthy yet. Check the startup log.', flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--snapshot', required=True, type=Path)
    parser.add_argument('--hermes-repo', required=True, type=Path)
    parser.add_argument('--port', type=int, default=17000)
    parser.add_argument('--hermes-port', type=int, default=18790)
    parser.add_argument('--hermes-python', type=Path, help='Hermes interpreter (round-trip YAML) for the provider-sync config writer')
    parser.add_argument('--no-browser', action='store_true')
    args = parser.parse_args()
    if not (1024 <= args.port <= 65535 and 1024 <= args.hermes_port <= 65535) or args.port == args.hermes_port:
        parser.error('Use distinct unprivileged development ports')
    if args.port in (7000, 8787) or args.hermes_port in (7000, 8787):
        parser.error('Production application ports are excluded from this prototype')
    repo = args.hermes_repo.resolve()
    if not (repo / 'start.ps1').is_file():
        parser.error('Hermes repository must contain the existing Windows start.ps1')
    sys.path.insert(0, str(ROOT))
    from src.application_modes import _port_is_free
    if not _port_is_free(args.port):
        parser.error('The development Odysseus port is occupied; no existing process was changed')
    state = prepare_state(ROOT, args.snapshot)
    extensions = state / 'extensions'
    shutil.copytree(ROOT / 'integrations/hermes-mode', extensions, dirs_exist_ok=True)
    odysseus_url = f'http://127.0.0.1:{args.port}/'
    hermes_url = f'http://127.0.0.1:{args.hermes_port}/'
    # Isolated component root: provider sync must never read or write the live component state.
    component_root = state / 'component'
    if args.hermes_python:
        (component_root / 'managed').mkdir(parents=True, exist_ok=True)
        (component_root / 'managed/component.json').write_text(json.dumps({
            'version': 1, 'enabled': True, 'origin': 'external', 'url': hermes_url, 'return_url': odysseus_url,
            'python': str(args.hermes_python.resolve()), 'home': str(state / 'hermes'), 'state_dir': str(state / 'hermes/webui'),
            'webui': str(repo), 'agent': str(Path(os.environ.get('LOCALAPPDATA', '')) / 'hermes/hermes-agent')}), encoding='utf-8')
    (extensions / 'connection.json').write_text(json.dumps({'odysseus_url': odysseus_url, 'hermes_url': hermes_url}), encoding='utf-8')
    environment = dict(os.environ)
    environment.update({
        'ODYSSEUS_DATA_DIR': str(state / 'odysseus'),
        'DATABASE_URL': 'sqlite:///' + (state / 'odysseus/app.db').as_posix(),
        'APP_PORT': str(args.port), 'APP_BIND': '127.0.0.1',
        'ODYSSEUS_INTERNAL_BASE': odysseus_url.rstrip('/'),
        'ODYSSEUS_MAIL_ATTACHMENTS_DIR': str(state / 'odysseus/mail-attachments'),
        'FASTEMBED_CACHE_PATH': str(state / 'odysseus/fastembed_cache'),
        'CHROMADB_HOST': '127.0.0.1', 'CHROMADB_PORT': '18100',
        'ODYSSEUS_STARTUP_WARMUPS': '0', 'ODYSSEUS_MODEL_KEEPALIVE': '0',
        'CLEANUP_ENABLED': 'false', 'PYTHONDONTWRITEBYTECODE': '1',
        'ODYSSEUS_HERMES_MODE_URL': hermes_url,
        'ODYSSEUS_HERMES_COMPONENT_ROOT': str(component_root),
        'ODYSSEUS_HERMES_ALLOW_START': '1',
        'ODYSSEUS_HERMES_REPO': str(repo),
        'ODYSSEUS_HERMES_HOME': str(state / 'hermes'),
        'ODYSSEUS_HERMES_STATE_DIR': str(state / 'hermes/webui'),
        'ODYSSEUS_HERMES_EXTENSION_DIR': str(extensions),
        'HERMES_WEBUI_COOKIE_NAME': 'hermes_mode_dev_session',
        'HERMES_WEBUI_CHAT_BACKEND': 'legacy',
    })
    # These inherited overrides could redirect the development backend to live state.
    for key in ('HERMES_WEBUI_SETTINGS_FILE', 'HERMES_WEBUI_PROFILE', 'HERMES_PROFILE'):
        environment.pop(key, None)
    if not args.no_browser:
        edge = Path(os.environ.get('ProgramFiles(x86)', 'C:/Program Files (x86)')) / 'Microsoft/Edge/Application/msedge.exe'
        if not edge.is_file():
            parser.error('Edge was not found; use --no-browser and a separate browser profile')
        threading.Thread(target=open_browser_when_ready, args=(edge, state / 'browser-profile', odysseus_url), daemon=True).start()
    print('Isolated Odysseus: ' + odysseus_url, flush=True)
    print('Hermes starts on demand at ' + hermes_url, flush=True)
    print('State: ' + str(state), flush=True)
    print('Prototype: background lifespan jobs are OFF; no production startup configuration is changed.', flush=True)
    # Disable existing schedulers, reapers, MCP startup and Cookbook lifecycle jobs
    # for a restored snapshot; they must never act on production references.
    return subprocess.call([sys.executable, '-B', '-m', 'uvicorn', 'app:app', '--host', '127.0.0.1', '--port', str(args.port), '--lifespan', 'off', '--no-proxy-headers'], cwd=ROOT, env=environment)


if __name__ == '__main__':
    raise SystemExit(main())

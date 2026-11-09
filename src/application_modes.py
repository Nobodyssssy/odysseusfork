"""Opt-in standalone Hermes connection; never merges application state."""
from dataclasses import dataclass
import http.client
import json
import os
from pathlib import Path
import socket
import subprocess
import threading
from urllib.parse import urlsplit


@dataclass(frozen=True)
class ModeConfig:
    url: str
    command: tuple[str, ...] = ()
    environment: dict[str, str] | None = None
    log_dir: Path | None = None

    @classmethod
    def from_environment(cls, environment=None):
        source = os.environ if environment is None else environment
        if source.get('ODYSSEUS_HERMES_DISABLED') == '1':
            return None
        from src.hermes_component import read_config
        from src.runtime_paths import get_app_root, get_hermes_component_root
        root = get_hermes_component_root(source)
        saved = read_config(Path(root) / 'managed/component.json') if root else None
        if saved and not saved['enabled']:
            return None
        url = source.get('ODYSSEUS_HERMES_MODE_URL')
        if not url:
            if not saved:
                return None
            child_env = dict(source)
            for key, target in [('home', 'HERMES_HOME'), ('state_dir', 'HERMES_WEBUI_STATE_DIR'), ('agent', 'HERMES_WEBUI_AGENT_DIR')]:
                child_env[target] = saved[key]
            if saved.get('runtime_dir'):
                child_env['HERMES_RUNTIME_DIR'] = saved['runtime_dir']
            child_env.setdefault('HERMES_WEBUI_PYTHON', saved['python'])
            command = (saved['python'], '-B', str(Path(get_app_root()) / 'scripts/hermes-component.py'), 'serve', '--root', str(root))
            return cls(saved['url'], command, child_env, Path(root) / 'managed/logs')
        config = cls(url=url)
        if source.get('ODYSSEUS_HERMES_ALLOW_START') != '1':
            return config
        repo = Path(source['ODYSSEUS_HERMES_REPO']).resolve()
        child_env = dict(source)
        for source_key, child_key in [
            ('ODYSSEUS_HERMES_HOME', 'HERMES_HOME'),
            ('ODYSSEUS_HERMES_STATE_DIR', 'HERMES_WEBUI_STATE_DIR'),
            ('ODYSSEUS_HERMES_EXTENSION_DIR', 'HERMES_WEBUI_EXTENSION_DIR'),
        ]:
            if source_key in source:
                child_env[child_key] = source[source_key]
        child_env['HERMES_WEBUI_HOST'] = '127.0.0.1'
        child_env['HERMES_WEBUI_PORT'] = str(urlsplit(url).port)
        if source.get('ODYSSEUS_HERMES_EXTENSION_DIR'):
            child_env['HERMES_WEBUI_EXTENSION_SCRIPT_URLS'] = '/extensions/return-to-odysseus.js'
            child_env['HERMES_WEBUI_EXTENSION_STYLESHEET_URLS'] = '/extensions/modes.css'
        command = ('powershell.exe', '-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass', '-File', str(repo / 'start.ps1'), '-BindHost', '127.0.0.1', '-Port', child_env['HERMES_WEBUI_PORT'])
        return cls(url=url, command=command, environment=child_env, log_dir=Path(source['ODYSSEUS_DATA_DIR']) / 'logs')

    def __post_init__(self):
        parsed = urlsplit(self.url)
        if not parsed.port or self.url != f'http://127.0.0.1:{parsed.port}/':
            raise ValueError('Hermes mode requires an exact http://127.0.0.1:PORT/ URL')


def _port_is_free(port):
    try:
        with socket.socket() as sock:
            if hasattr(socket, 'SO_EXCLUSIVEADDRUSE'):
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
            sock.bind(('127.0.0.1', port))
        return True
    except OSError:
        return False


class ModeService:
    def __init__(self, config, error=None):
        self.config = config
        self.error = error
        self.proxy = False
        self._lock = threading.Lock()
        self._process = None

    def start(self):
        with self._lock:
            if self.error:
                raise RuntimeError(self.error)
            current = self.status()
            if current.get('state') == 'ready':
                return current
            if not self.config or current.get('state') != 'stopped':
                raise RuntimeError('Hermes listener is unavailable or belongs to another service; no restart attempted')
            if self._process is not None and self._process.poll() is None:
                return {**current, 'state': 'starting'}
            if not self.config.command or not self.config.log_dir:
                raise RuntimeError('Optional Hermes startup is not configured')
            self.config.log_dir.mkdir(parents=True, exist_ok=True)
            flags = (subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW) if os.name == 'nt' else 0
            with (self.config.log_dir / 'hermes-launch.log').open('ab') as log:
                self._process = subprocess.Popen(
                    list(self.config.command), env=self.config.environment or os.environ.copy(),
                    stdin=subprocess.DEVNULL, stdout=log, stderr=log,
                    creationflags=flags, start_new_session=os.name != 'nt',
                )
            return {**current, 'state': 'starting'}

    def status(self):
        if self.error:
            return {'enabled': True, 'state': 'unavailable', 'error': self.error}
        if self.config is None:
            return {'enabled': False}
        port = urlsplit(self.config.url).port
        result = {'enabled': True, 'url': self.config.url, 'owned': False}
        if self.proxy:
            from src.hermes_proxy import hostname_resolves
            result['proxy'] = hostname_resolves()
        connection = http.client.HTTPConnection('127.0.0.1', port, timeout=2)
        try:
            connection.request('GET', '/health')
            response = connection.getresponse()
            body = response.read(65537)
            health = json.loads(body) if len(body) <= 65536 else None
            identity = isinstance(health, dict) and isinstance(health.get('sessions'), int) and isinstance(health.get('server_started_at'), (int, float))
            ready = identity and response.status == 200 and health.get('status') == 'ok'
            result['state'] = 'ready' if ready else ('unavailable' if identity else 'conflict')
            if identity:
                result['active_runs'] = health.get('active_runs')
                result['server_started_at'] = health['server_started_at']
        except (OSError, http.client.HTTPException, ValueError):
            result['state'] = 'stopped' if _port_is_free(port) else 'unavailable'
        finally:
            connection.close()
        return result


def create_mode_service(environment=None):
    from src.hermes_component import ComponentError
    try:
        return ModeService(ModeConfig.from_environment(environment))
    except (ComponentError, ValueError, KeyError, OSError):
        return ModeService(None, 'Hermes setup is unavailable. Run scripts/hermes-component.py repair; Normal Mode remains available.')


def require_local(request):
    """Loopback peer, loopback hostname, no proxy headers."""
    from fastapi import HTTPException
    forwarded = any(name in request.headers for name in ('forwarded', 'x-forwarded-for', 'x-forwarded-host', 'x-forwarded-proto', 'x-real-ip'))
    if forwarded or not request.client or request.client.host not in ('127.0.0.1', '::1') or request.url.hostname not in ('127.0.0.1', 'localhost'):
        raise HTTPException(403, 'Application modes are local-only')


def require_browser_admin(request):
    """A same-origin action by a direct local browser administrator; never the Agent, an API token or a tool identity."""
    from fastapi import HTTPException
    from core.middleware import require_admin
    require_local(request)
    origin = f'{request.url.scheme}://{request.url.netloc}'
    if request.headers.get('origin') != origin:
        raise HTTPException(403, 'Same-origin browser action required')
    if getattr(request.state, 'current_user', None) in ('internal-tool', 'api', 'demo', 'system') or 'authorization' in request.headers or 'x-odysseus-internal-token' in request.headers:
        raise HTTPException(403, 'A direct local browser administrator is required')
    require_admin(request)


def make_router(service):
    from fastapi import APIRouter, HTTPException, Request

    router = APIRouter(prefix='/api/application-modes', tags=['application-modes'])
    local_only = require_local

    @router.get('')
    def status(request: Request):
        local_only(request)
        return service.status()

    @router.post('/hermes/start')
    def start(request: Request):
        require_browser_admin(request)
        try:
            return service.start()
        except RuntimeError as exc:
            raise HTTPException(409, str(exc)) from exc
        except OSError as exc:
            raise HTTPException(503, 'Hermes launch failed; inspect the private launch log') from exc

    return router

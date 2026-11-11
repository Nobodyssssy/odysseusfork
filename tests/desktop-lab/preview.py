"""Disposable Odysseus preview in front of the isolated official Hermes Webapp (lab only; touches no production state).

    python preview.py setup     fresh state dir, synthetic admin + browser session secret (never printed)
    python preview.py start     real Odysseus app on 127.0.0.1:ODY_PORT, gateway proxy mode, lifespan jobs OFF
    python preview.py status | stop | clean

Isolation: its own data dir/DB/auth/sessions, a minimal environment (no inherited ODYSSEUS_*/HERMES_* variables, HOME/APPDATA
redirected), a marker .env in the worktree so python-dotenv cannot find F:\Odysseus\.env, no schedulers/warm-ups/keep-alive,
loopback bind only, ports distinct from 7000/8100/8787/9120.
"""
import json
import os
from pathlib import Path
import secrets
import shutil
import subprocess
import sys
import time

LAB = Path(r'F:\Odysseus-Backups\desktop-lab')
PV = LAB / 'odysseus-preview'
APP = Path(os.environ.get('PREVIEW_APP', r'F:\Odysseus\integration-backups\webapp-proxy'))
FULL = LAB / 'ody-full-venv/Scripts/python.exe'
HERMES_PY = Path(r'C:\Users\Protagonist\AppData\Local\hermes\installs\562e58e2ea9aca80\environments\6eff8cc60cb848909fbabec0b09c88c2\venv\Scripts\python.exe')
AGENT = LAB / 'src'
GATEWAY_HOME = LAB / 'home'
MANAGED = os.environ.get('PREVIEW_MANAGED_ROOT')           # a component root installed by hermes-webapp-component.py: Odysseus starts the gateway itself
ODY_PORT, GW_PORT = 19800, (19130 if MANAGED else 19120)
ENV_MARKER = '# Isolated preview; configuration is supplied by preview.py.\n'
ADMIN = 'labadmin'
FORBIDDEN = {7000, 8100, 8787, 9120}


def child_env():
    keep = ('SystemRoot', 'SYSTEMROOT', 'SystemDrive', 'windir', 'ComSpec', 'PATHEXT', 'OS', 'PROCESSOR_ARCHITECTURE', 'NUMBER_OF_PROCESSORS')
    env = {k: os.environ[k] for k in keep if k in os.environ}
    env['PATH'] = str(FULL.parent) + ';C:\\Program Files\\Git\\cmd;C:\\Windows\\System32;C:\\Windows'
    for key in ('TEMP', 'TMP'):
        env[key] = str(PV / 'tmp')
    for key in ('USERPROFILE', 'HOME'):
        env[key] = str(PV / 'home')
    env['APPDATA'] = str(PV / 'home/AppData/Roaming')
    env['LOCALAPPDATA'] = str(PV / 'home/AppData/Local')
    base = f'http://127.0.0.1:{ODY_PORT}'
    env.update({
        'ODYSSEUS_DATA_DIR': str(PV / 'data'), 'DATABASE_URL': 'sqlite:///' + (PV / 'data/app.db').as_posix(),
        'APP_PORT': str(ODY_PORT), 'APP_BIND': '127.0.0.1', 'ODYSSEUS_INTERNAL_BASE': base,
        'ODYSSEUS_MAIL_ATTACHMENTS_DIR': str(PV / 'data/mail-attachments'), 'FASTEMBED_CACHE_PATH': str(PV / 'data/fastembed_cache'),
        'CHROMADB_HOST': '127.0.0.1', 'CHROMADB_PORT': '18100', 'ODYSSEUS_STARTUP_WARMUPS': '0', 'ODYSSEUS_MODEL_KEEPALIVE': '0',
        'CLEANUP_ENABLED': 'false', 'PYTHONDONTWRITEBYTECODE': '1', 'ODYSSEUS_INPROCESS_TASKS': '0', 'ODYSSEUS_INPROCESS_POLLERS': '0',
        'ODYSSEUS_HERMES_COMPONENT_ROOT': str(PV / 'component'),
        'ODYSSEUS_HERMES_BACKEND': 'gateway', 'ODYSSEUS_HERMES_MODE_URL': f'http://127.0.0.1:{GW_PORT}/',
        'ODYSSEUS_HERMES_GATEWAY_HOME': str(GATEWAY_HOME), 'ODYSSEUS_HERMES_GATEWAY_PYTHON': str(HERMES_PY),
        'ODYSSEUS_HERMES_GATEWAY_AGENT_DIR': str(AGENT), 'ODYSSEUS_HERMES_PROXY_MODE': 'gateway',
    })
    if MANAGED:
        for key in ('ODYSSEUS_HERMES_MODE_URL', 'ODYSSEUS_HERMES_GATEWAY_HOME', 'ODYSSEUS_HERMES_GATEWAY_PYTHON', 'ODYSSEUS_HERMES_GATEWAY_AGENT_DIR'):
            env.pop(key)
        env.update({'ODYSSEUS_HERMES_COMPONENT_ROOT': MANAGED, 'ODYSSEUS_HERMES_BACKEND': 'webapp'})
    env.update({k[len('PREVIEW_ENV_'):]: v for k, v in os.environ.items() if k.startswith('PREVIEW_ENV_')})
    return env


def guard():
    assert ODY_PORT not in FORBIDDEN and GW_PORT not in FORBIDDEN
    assert str(PV).lower().startswith(str(LAB).lower())
    assert 'Odysseus-Backups' in str(GATEWAY_HOME)
    assert not MANAGED or str(MANAGED).lower().startswith(str(LAB).lower())


def setup():
    guard()
    if PV.exists():
        shutil.rmtree(PV)
    for sub in ('data', 'component', 'logs', 'tmp', 'home/AppData/Roaming', 'home/AppData/Local'):
        (PV / sub).mkdir(parents=True)
    env_file = APP / '.env'
    if env_file.exists() and env_file.read_text(encoding='utf-8-sig') != ENV_MARKER:
        raise SystemExit('Refusing to replace an existing .env in the worktree')
    env_file.write_text(ENV_MARKER, encoding='utf-8')
    password, = (secrets.token_urlsafe(24),)
    code = ("import sys,json;sys.path.insert(0,r'%s');from core.auth import AuthManager;am=AuthManager();"
            "assert am.setup(%r,sys.argv[1]);t=am.create_session_trusted(%r);print(json.dumps({'token':t}))" % (APP, ADMIN, ADMIN))
    out = subprocess.run([str(FULL), '-B', '-c', code, password], cwd=APP, env=child_env(), capture_output=True, text=True, timeout=120)
    if out.returncode:
        raise SystemExit('admin setup failed:\n' + out.stderr[-1500:])
    token = json.loads(out.stdout.strip().splitlines()[-1])['token']
    (PV / 'lab-admin.secret').write_text(json.dumps({'username': ADMIN, 'password': password, 'session': token}), encoding='utf-8')
    print('setup ok: state', PV, '| admin + session stored in lab-admin.secret (not shown)')


def pid_file():
    return PV / 'odysseus.pid'


def start():
    guard()
    command = [str(FULL), '-B', '-m', 'uvicorn', 'app:app', '--host', '127.0.0.1', '--port', str(ODY_PORT), '--lifespan', 'off', '--no-proxy-headers',
               '--ws', 'websockets', '--ws-max-size', str(400 * 1024 * 1024), '--log-level', 'info']
    log = open(PV / 'logs/odysseus.log', 'ab')
    flags = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW
    proc = subprocess.Popen(command, cwd=APP, env=child_env(), stdin=subprocess.DEVNULL, stdout=log, stderr=log, creationflags=flags)
    pid_file().write_text(str(proc.pid))
    print('started pid', proc.pid)


def listening(port):
    out = subprocess.run(['netstat', '-ano', '-p', 'TCP'], capture_output=True, text=True).stdout
    return any(f'127.0.0.1:{port} ' in line and 'LISTENING' in line for line in out.splitlines())


def stop():
    if pid_file().exists():
        pid = pid_file().read_text().strip()
        print(subprocess.run(['taskkill', '/PID', pid, '/T', '/F'], capture_output=True, text=True).stdout.strip()[:120])
        pid_file().unlink()


def clean():
    stop()
    env_file = APP / '.env'
    if env_file.exists() and env_file.read_text(encoding='utf-8-sig') == ENV_MARKER:
        env_file.unlink()
    if PV.exists():
        shutil.rmtree(PV, ignore_errors=True)
    print('cleaned; state dir exists:', PV.exists(), '| .env exists:', env_file.exists())


if __name__ == '__main__':
    {'setup': setup, 'start': start, 'stop': stop, 'clean': clean,
     'status': lambda: print({'odysseus': listening(ODY_PORT), 'gateway': listening(GW_PORT)})}[sys.argv[1]]()

"""Lab controller. Everything it starts is sandboxed or synthetic; it never touches production paths or ports.

    python lab.py setup plugin_on|plugin_off
    python lab.py start | stop | status
Gateway: Low-integrity process (OS-enforced write boundary), throwaway HERMES_HOME, Webapp on 19120, API server on 18642.
"""
import json
import os
import secrets
import shutil
import subprocess
import sys
import time
import urllib.request

LAB = r'F:\Odysseus-Backups\desktop-lab'
SRC = os.environ.get('LAB_AGENT_SRC') or (LAB + r'\src')                 # LAB_AGENT_SRC: run the lab gateway from another agent checkout (e.g. the route-guard tree)
DIST = LAB + r'\src\apps\desktop\dist-webapp'
VENV = r'C:\Users\Protagonist\AppData\Local\hermes\installs\562e58e2ea9aca80\environments\6eff8cc60cb848909fbabec0b09c88c2\venv'
PY = VENV + r'\Scripts\python.exe'
HOME, SAFE, OUT, CWD = (LAB + p for p in (r'\home', r'\work\safe', r'\work\outside', r'\work\cwd'))
REPO, BARE = LAB + r'\repos\work', LAB + r'\repos\bare.git'
PLUGIN_SRC = r'F:\Odysseus\integration-backups\phase1\integrations\hermes-mode\plugins\odysseus-ask-policy'
GIT = r'C:\Program Files\Git\cmd\git.exe'
PORTS = {'webapp': 19120, 'api': 18642, 'llm': 18700}
STATE = LAB + r'\results\lab-state.json'
API_KEY = None


def run(cmd, **kw):
    return subprocess.run(cmd, capture_output=True, text=True, creationflags=0x08000000, **kw)


def setup(variant, wide=False):
    for d in (SAFE, OUT, REPO, BARE):
        shutil.rmtree(d, ignore_errors=True)
    if os.path.isdir(HOME):                                  # keep the labelled root: only empty it
        for name in os.listdir(HOME):
            full = os.path.join(HOME, name)
            shutil.rmtree(full, ignore_errors=True) if os.path.isdir(full) else os.remove(full)
    for d in (HOME + r'\plugins', HOME + r'\vault', SAFE + r'\secrets', OUT, CWD, LAB + r'\repos'):
        os.makedirs(d, exist_ok=True)
    open(SAFE + r'\hello.txt', 'w').write('hello from the synthetic safe folder\n')
    open(SAFE + r'\secrets\prod.env', 'w').write('SYNTHETIC_KEY=not-a-real-secret\n')
    os.makedirs(SAFE + r'\victim', exist_ok=True)
    open(SAFE + r'\victim\a.txt', 'w').write('synthetic victim file\n')
    json.dump({'version': 1, 'trusted': [], 'blocked': [{'path': SAFE + r'\secrets'}, {'path': HOME + r'\plugins'}]}, open(HOME + r'\vault\webui-trusted-workspaces.json', 'w'))
    # synthetic git repo with a local bare "remote"
    run([GIT, 'init', '-q', '--bare', BARE])
    run([GIT, 'init', '-q', REPO])
    for k, v in (('user.name', 'lab'), ('user.email', 'lab@example.invalid'), ('commit.gpgsign', 'false')):
        run([GIT, '-C', REPO, 'config', k, v])
    open(REPO + r'\README.md', 'w').write('synthetic repo\n')
    run([GIT, '-C', REPO, 'add', '.'])
    run([GIT, '-C', REPO, 'commit', '-q', '-m', 'init'])
    run([GIT, '-C', REPO, 'remote', 'add', 'origin', BARE])
    plugins = ''
    if variant == 'plugin_on':
        shutil.copytree(PLUGIN_SRC, HOME + r'\plugins\odysseus-ask-policy', ignore=shutil.ignore_patterns('tests', '__pycache__'))
        plugins = 'plugins:\n  enabled:\n    - odysseus-ask-policy\n'
    config = f'''model:
  provider: custom
  default: stub-model
  base_url: 'http://127.0.0.1:{PORTS['llm']}/v1'
  api_key: lab-not-a-real-key
webapp:
  enabled: true
  host: 127.0.0.1
  port: {PORTS['webapp']}
  public_url: ""
terminal:
  backend: local
  cwd: '{CWD}'
approvals:
  mode: manual
  timeout: 8
command_allowlist: []
mcp_servers:
  stubmcp:
    command: '{PY}'
    args: ['{LAB}\\tools\\stub_mcp.py']
{plugins}'''
    open(HOME + r'\config.yaml', 'w').write(config)
    for d in (HOME, SAFE, OUT, CWD, REPO):
        assert 'Low Mandatory' in run(['icacls', d]).stdout, f'{d} lost its Low label'

    state = json.load(open(STATE)) if os.path.exists(STATE) else {}
    state.update(variant=variant, wide=wide, api_key=state.get('api_key') or secrets.token_urlsafe(24))
    json.dump(state, open(STATE, 'w'))
    print('setup', variant, 'ok; plugin copied:', os.path.isdir(HOME + r'\plugins\odysseus-ask-policy'))


def gateway_env(api_key):
    sysroot = os.environ.get('SystemRoot', r'C:\Windows')
    return {
        'SystemRoot': sysroot, 'ComSpec': sysroot + r'\System32\cmd.exe', 'PATHEXT': '.COM;.EXE;.BAT;.CMD',
        'PATH': ';'.join([VENV + r'\Scripts', r'C:\Program Files\Git\cmd', r'C:\Program Files\Git\usr\bin', sysroot + r'\System32', sysroot,
                          sysroot + r'\System32\WindowsPowerShell\v1.0']),
        'USERPROFILE': LAB + r'\userhome', 'HOME': LAB + r'\userhome', 'APPDATA': LAB + r'\userhome\AppData\Roaming',
        'LOCALAPPDATA': LAB + r'\userhome\AppData\Local', 'TEMP': LAB + r'\tmp', 'TMP': LAB + r'\tmp',
        'HERMES_HOME': HOME, 'HERMES_WRITE_SAFE_ROOT': ((SAFE + os.pathsep + HOME) if json.load(open(STATE)).get('wide') else SAFE) + ((os.pathsep + CWD) if os.environ.get('LAB_SAFE_CWD') else ''), 'PYTHONPATH': SRC, 'PYTHONDONTWRITEBYTECODE': '1', 'PYTHONUTF8': '1',
        'HERMES_WEBAPP_DIST': DIST, 'API_SERVER_ENABLED': 'true', 'API_SERVER_KEY': api_key,
        'API_SERVER_PORT': str(PORTS['api']), 'API_SERVER_HOST': '127.0.0.1', 'HERMES_ACCEPT_HOOKS': '1',
    }


def start():
    state = json.load(open(STATE))
    stub = subprocess.Popen([sys.executable, LAB + r'\tools\stub_llm.py', str(PORTS['llm'])], creationflags=0x08000000 | 0x00000008,
                            stdout=open(LAB + r'\logs\stub-llm.out', 'w'), stderr=subprocess.STDOUT)
    env = gateway_env(state['api_key'])
    args = [sys.executable, LAB + r'\tools\lowil_run.py', '--cwd', CWD, '--log', LAB + r'\logs\gateway.log', '--detach']
    for k, v in env.items():
        args += ['--env', f'{k}={v}']
    args += ['--', PY, '-W', 'ignore', '-m', 'gateway.run']
    launcher_out = LAB + r'\logs\launcher.out'
    with open(launcher_out, 'w') as sink:                      # a file, not a pipe: the long-lived child must not keep a pipe open
        subprocess.run(args, stdout=sink, stderr=subprocess.STDOUT, creationflags=0x08000000, timeout=60)
    print(open(launcher_out).read().strip()[-300:])
    state.update(stub_pid=stub.pid)
    json.dump(state, open(STATE, 'w'))


def stop():
    killed = []
    pidfile = HOME + r'\gateway.pid'
    if os.path.exists(pidfile):                              # the LAB gateway's own pid file: never match on "gateway.run" (production runs that too)
        try:
            pid = json.load(open(pidfile)).get('pid')
            if pid and 'desktop-lab' in open(pidfile).read():
                run(['taskkill', '/PID', str(pid), '/T', '/F'])
                killed.append(pid)
        except (OSError, ValueError):
            pass
    out = run(['powershell', '-NoProfile', '-Command',
               "Get-CimInstance Win32_Process | ? { $_.CommandLine -match 'desktop-lab.tools.(stub_llm|stub_mcp)' } | % { Stop-Process -Id $_.ProcessId -Force; $_.ProcessId }"], timeout=60)
    killed += out.stdout.split()
    print('stopped lab pids:', killed)


def status():
    for name, port in PORTS.items():
        r = run(['netstat', '-ano', '-p', 'TCP']).stdout
        pids = [l.split()[4] for l in r.splitlines() if f'127.0.0.1:{port} ' in l and 'LISTENING' in l]
        print(f'{name:7} {port}: {"listening pid " + pids[0] if pids else "-"}')


if __name__ == '__main__':
    cmd = sys.argv[1]
    {'setup': lambda: setup(sys.argv[2], len(sys.argv) > 3 and sys.argv[3] == 'wide'), 'start': start, 'stop': stop, 'status': status}[cmd]()

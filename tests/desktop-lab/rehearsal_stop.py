"""Stop the migration rehearsal (preview Odysseus 19800, managed gateway 19130, stub model 18700). Touches nothing outside this lab folder.

    python tools/rehearsal_stop.py                 stop everything, keep the migrated data
    python tools/rehearsal_stop.py --delete-data   stop, then delete the migrated copy of your Hermes data and the preview state
"""
import os
import shutil
import subprocess
import sys
import time

LAB = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ROOT = os.path.join(LAB, 'smoke-root')
APP = r'F:\Odysseus\integration-backups\webapp-proxy'
PY = os.path.join(LAB, 'ody-full-venv', 'Scripts', 'python.exe')
ENV = {k: v for k, v in os.environ.items() if k not in ('PYTHONPATH', 'HERMES_HOME')}
ENV['PREVIEW_MANAGED_ROOT'] = ROOT


def run(*args):
    return subprocess.run([PY, '-W', 'ignore', *args], env=ENV, capture_output=True, text=True, timeout=120)


def listening(port):
    out = subprocess.run(['netstat', '-ano', '-p', 'TCP'], capture_output=True, text=True).stdout
    return [line.split()[4] for line in out.splitlines() if f'127.0.0.1:{port} ' in line and 'LISTENING' in line]


run(os.path.join(LAB, 'tools', 'preview.py'), 'stop')
run(os.path.join(APP, 'scripts', 'hermes-webapp-component.py'), 'stop', '--force', '--root', ROOT)
for pid in listening(18700):
    subprocess.run(['taskkill', '/PID', pid, '/F'], capture_output=True)
time.sleep(2)
print('still listening:', {p: bool(listening(p)) for p in (19800, 19130, 18700)})

if '--delete-data' in sys.argv:
    run(os.path.join(LAB, 'tools', 'preview.py'), 'clean')
    for path in (os.path.join(ROOT, 'state'), os.path.join(LAB, 'results', 'rehearsal'), os.path.join(LAB, 'scratch-profile'), os.path.join(ROOT, 'migration')):
        if os.path.abspath(path).lower().startswith(LAB.lower() + os.sep) and os.path.exists(path):
            shutil.rmtree(path, ignore_errors=True)
            print('deleted', path)

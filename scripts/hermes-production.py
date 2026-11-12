"""Production readiness and the one-line switch for Projects mode (the managed official Hermes Webapp).

    plan                  READ-ONLY. Checks production and prints the ordered, reversible procedure. Writes nothing, installs nothing, starts nothing.
    env-on  --approved    add ODYSSEUS_HERMES_BACKEND=webapp to the production .env (a copy of the file is kept first)
    env-off --approved    remove that line again: this is the rollback of the switch (restart Odysseus afterwards)

`env-on` and `env-off` change only that one line. Neither restarts anything: restarting Odysseus is a separate, deliberate step.
Every other production step (dependency, code, component install, data migration) is a command listed by `plan`, run one at a time.
"""
import argparse
import http.client
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

APP_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP_ROOT))

from src import hermes_production as production
from src.runtime_paths import get_hermes_component_root

PINS = APP_ROOT / 'config' / 'production-dependencies.json'
CUTOVER = APP_ROOT / 'config' / 'production-cutover.json'
GIT = shutil.which('git') or r'C:\Program Files\Git\cmd\git.exe'
GIB = 1 << 30


def git(root, *args):
    result = subprocess.run([GIT, '-C', str(root), *args], capture_output=True, text=True, timeout=60,
                            env={**os.environ, 'GIT_TERMINAL_PROMPT': '0', 'GIT_OPTIONAL_LOCKS': '0'})
    return result.returncode, result.stdout.strip()


def http_status(port, path='/'):
    try:
        connection = http.client.HTTPConnection('127.0.0.1', port, timeout=3)
        connection.request('GET', path)
        return connection.getresponse().status
    except OSError:
        return None


def listener_chain(port):
    """Read-only: the process listening on ``port`` and its parents (what a restart of Odysseus has to stop)."""
    script = ("$id=(Get-NetTCPConnection -LocalPort %d -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1).OwningProcess;"
              "while($id -and $id -ne 0){$p=Get-CimInstance Win32_Process -Filter \"ProcessId=$id\";if(-not $p){break};"
              "'{0}|{1}|{2}' -f $p.ProcessId,$p.Name,($p.CommandLine -replace '\\s+',' ');$id=$p.ParentProcessId;if($p.Name -match 'explorer|svchost|services|wininit'){break}}" % port)
    result = subprocess.run(['powershell', '-NoProfile', '-Command', script], capture_output=True, text=True, timeout=30)
    return [line.split('|', 2) for line in result.stdout.strip().splitlines() if line.count('|') >= 2]


def plan(args):
    prod = Path(args.odysseus_root)
    cutover = json.loads(CUTOVER.read_text(encoding='utf-8'))
    target = args.target or cutover['deploy_branch']
    rows = []

    def row(state, name, detail):
        rows.append((state, name, detail))

    code, head = git(prod, 'rev-parse', '--short', 'HEAD')
    _, branch = git(prod, 'rev-parse', '--abbrev-ref', 'HEAD')
    _, dirty = git(prod, 'status', '--porcelain')
    _, ahead = git(prod, 'rev-list', '--count', f'HEAD..{target}')
    _, target_head = git(prod, 'rev-parse', '--short', target)
    ff = git(prod, 'merge-base', '--is-ancestor', 'HEAD', target)[0] == 0
    row('OK' if code == 0 and not dirty else 'BLOCK', 'production checkout', f'{branch} @ {head}, {"clean" if not dirty else "HAS UNCOMMITTED CHANGES"}')
    row('TODO' if ff and ahead != '0' else ('OK' if ff else 'BLOCK'), 'code to deploy',
        f'{target} @ {target_head} is {ahead} commit(s) ahead and a pure fast-forward' if ff else f'{target} is NOT a fast-forward of production; merge needed')

    ok, detail = production.verify_wheel(PINS)
    row('OK' if ok else 'BLOCK', 'websockets wheel staged', detail)
    pins = json.loads(PINS.read_text(encoding='utf-8'))['websockets']
    venv_python = prod / 'venv' / 'Scripts' / 'python.exe'
    probe = ('import importlib.metadata as m, sys\n'
             'try: w = m.version("websockets")\nexcept Exception: w = "missing"\n'
             'print(sys.version.split()[0], w)')
    result = subprocess.run([str(venv_python), '-B', '-c', probe], capture_output=True, text=True, timeout=30) if venv_python.is_file() else None
    if result and result.returncode == 0:
        python_version, installed = result.stdout.split()
        row('OK' if installed == pins['version'] else 'TODO', 'production venv', f"Python {python_version}; websockets {installed} (pinned {pins['version']})")
        if installed != pins['version']:
            dry = subprocess.run([str(venv_python), '-B', '-m', 'pip', 'install', '--dry-run', '--no-index', '--no-cache-dir', '--disable-pip-version-check', '--find-links',
                                  pins['staged_in'], f"websockets=={pins['version']}"], capture_output=True, text=True, timeout=120)
            row('OK' if dry.returncode == 0 and 'Would install' in dry.stdout else 'BLOCK', 'offline install resolves (dry run, nothing installed)',
                (dry.stdout.strip().splitlines() or ['?'])[-1] if dry.returncode == 0 else dry.stderr.strip()[-120:])
        imports = subprocess.run([str(venv_python), '-B', '-c', f'import sys; sys.path.insert(0, r"{APP_ROOT}"); import src.hermes_webapp_component, src.hermes_migration, src.hermes_production, src.hermes_proxy'],
                                 capture_output=True, text=True, timeout=60)
        row('OK' if imports.returncode == 0 else 'BLOCK', 'cutover tools import under the production Python', 'ok' if imports.returncode == 0 else imports.stderr.strip()[-160:])
    else:
        row('BLOCK', 'production venv', f'{venv_python} could not be run')

    value = production.current(prod / '.env')
    row('OK', '.env switch', f'ODYSSEUS_HERMES_BACKEND is {"not set (old behaviour)" if value is None else value}')

    hermes_python = Path(cutover['hermes_python'])
    row('OK' if hermes_python.is_file() else 'BLOCK', 'Hermes interpreter for the managed gateway', str(hermes_python) if hermes_python.is_file() else f'{hermes_python} does not exist (Hermes was updated?): fix config/production-cutover.json')

    root = get_hermes_component_root()
    component = None
    if root:
        from src.hermes_webapp_component import WebappComponent
        component = WebappComponent(root, application_root=APP_ROOT, environment=dict(os.environ))
        try:
            status = dict(component.status())
        except Exception as exc:                                                   # a read-only report never stops at one failing check
            status = {'state': 'unreadable', 'detail': str(exc)[:120]}
        if status.get('state') == 'not_installed':
            row('TODO', 'managed Hermes component', f'not installed under {root}')
        else:
            row('OK' if status.get('route_guard') == 'applied' else 'BLOCK', 'managed Hermes component',
                f"{status.get('state')}, route guard {status.get('route_guard')}, revision {str(status.get('revision'))[:8]}")
        free = shutil.disk_usage(Path(root).anchor or root).free
        row('OK' if free > 4 * GIB else 'BLOCK', 'disk for the install', f'{free / GIB:.1f} GB free on {Path(root).anchor} (about 1.3 GB needed)')
        row('OK', 'managed gateway write roots', 'none: you authorize project folders later; nothing is read or touched on any project')
        row('TODO', 'Claude/Codex logins', ', '.join(cutover['logins_at_cutover']) + ' allowed at step 5 (isolated until then); credentials stay where they are')
    else:
        row('BLOCK', 'managed Hermes component', 'no component root')

    snapshots = sorted(Path(cutover['backup_dir']).glob('snapshot-*'))
    if snapshots:
        stamp = json.loads((snapshots[-1] / 'manifest.json').read_text(encoding='utf-8')).get('created_at', '?')[:16]
        row('OK', 'latest backup on disk', f'{snapshots[-1].name} ({stamp}); a fresh one is made and verified at step 1')
    else:
        row('BLOCK', 'latest backup on disk', f"none in {cutover['backup_dir']}")
    old = http_status(cutover['old_webui_port'])
    row('OK' if old else 'BLOCK', 'old Hermes WebUI (the rollback)', f"answers on {cutover['old_webui_port']}" if old else 'NOT answering: the rollback would not be immediate')
    row('OK' if http_status(7000, '/login') else 'BLOCK', 'Odysseus', 'answers on 7000')
    row('OK', f"port {cutover['gateway_port']} for the managed gateway", 'free' if http_status(cutover['gateway_port']) is None else 'in use (fine if it is the managed gateway)')
    chain = listener_chain(7000)
    launcher = next((c for c in chain if c[1].lower().startswith('powershell')), None)
    row('OK' if launcher else 'BLOCK', 'restart target (Odysseus launcher)', f'PID {launcher[0]}: {launcher[2][:110]}' if launcher else 'could not find the launcher that owns the listener on 7000')
    user_var = subprocess.run(['powershell', '-NoProfile', '-Command', "[Environment]::GetEnvironmentVariable('HERMES_WRITE_SAFE_ROOT','User')"], capture_output=True, text=True, timeout=30).stdout.strip()
    row('NOTE', 'old WebUI write roots (unchanged, separate)', user_var or 'not set')

    print('PRODUCTION READINESS (read-only: nothing is installed, written or restarted)\n')
    for state, name, detail in rows:
        print(f'  [{state:5}] ' + (name.ljust(48) + ' ' if name else ' ' * 49) + detail)
    blockers = [name for state, name, _ in rows if state == 'BLOCK']
    print(f'\n{len(blockers)} blocker(s)' + (': ' + ', '.join(blockers) if blockers else '') + f'; {sum(1 for r in rows if r[0] == "TODO")} step(s) to do at cutover; {sum(1 for r in rows if r[0] == "WAIT")} waiting for your decision.')
    lab = Path(r'F:\Odysseus-Backups\desktop-lab')
    values = dict(prod=prod, target=target, head=head, venv=venv_python, app=APP_ROOT, wheels=pins['staged_in'], version=pins['version'], hpy=cutover['hermes_python'], home=(Path(root) / 'state' / 'hermes-gateway') if root else '<home>',
                  port=cutover['gateway_port'], backups=cutover['backup_dir'], roots=' '.join(f'--add-root {r}' for r in cutover['approved_write_roots']),
                  logins=' '.join(f'--allow {n}' for n in cutover['logins_at_cutover']), lab_python=lab / 'ody-full-venv' / 'Scripts' / 'python.exe', lab_logs=lab / 'logs',
                  hermes_home=Path(os.environ.get('LOCALAPPDATA', '')) / 'hermes', component_root=root or '<component root>', launcher=launcher[0] if launcher else '<launcher pid>',
                  old_port=cutover['old_webui_port'])
    print(PROCEDURE.format(**values))
    return 1 if blockers else 0


PROCEDURE = r"""
PROCEDURE after you say GO (re-run this plan first: every row OK; then one step at a time, each can be undone, stop after any of them)
  1 BACKUP         {venv} {app}\scripts\odysseus-backup.py --backup-dir {backups} --odysseus-root {prod} --data-dir {prod}\data create --exclude-secrets
                   (no credential file is copied and secret values are removed from the copies; your existing full backups stay as they are. Rollback does not need the secrets: nothing in the live Hermes home or Odysseus is modified)
                   {venv} {app}\scripts\odysseus-backup.py --backup-dir {backups} --odysseus-root {prod} --data-dir {prod}\data verify <snapshot name printed by create>
  2 RELEASE CHECK  {lab_python} {app}\scripts\hermes-release-check.py run --python {lab_python} --repo {app} --log {lab_logs}\release-check.log      (about 16 minutes, once)
                   must print "RELEASE CHECK: CLEAN"; after it, no commit may be added to {target}
  3 DEPENDENCY     {venv} -m pip install --no-index --no-cache-dir --find-links {wheels} websockets=={version}
                   undo: {venv} -m pip uninstall -y websockets
  4 GATEWAY        {venv} {app}\scripts\hermes-webapp-component.py ensure --origin managed --python {hpy} --home {home} --port {port}      (about 12 minutes; does not start it)
                   undo: delete {component_root}\managed\webapp and {home}
  5 AUTHORIZE      {venv} {app}\scripts\hermes-webapp-component.py logins {logins}      (write roots stay empty: Projects can read nothing it was not given and write nothing until you add a folder)
                   undo: logins --deny claude --deny codex
  6 DATA           {venv} {app}\scripts\hermes-migrate.py bundle --source {hermes_home} --out {backups}\hermes-bundle-<yyyymmdd-hhmm> --python {hpy}
                   {venv} {app}\scripts\hermes-migrate.py plan --bundle <that folder>        must say "problems": []
                   {venv} {app}\scripts\hermes-migrate.py apply --bundle <that folder> --confirm-target {home}
                   {venv} {app}\scripts\hermes-migrate.py verify --bundle <that folder>
                   undo: hermes-migrate.py rollback --safety-copy <pre-migration zip printed by apply> --confirm-target {home}
                   (do not start new chats in the old WebUI between the bundle and step 8: they would not be in the bundle)
  7 SWITCH         git -C {prod} merge --ff-only {target}                                undo: git -C {prod} reset --hard {head}
                   {venv} {app}\scripts\hermes-production.py env-on --approved
                   taskkill /PID {launcher} /T /F        then        Start-ScheduledTask -TaskName 'Odysseus AutoStart'
                   (needs: no active Odysseus chat or agent run; the old WebUI and the old gateway keep running)
  8 VERIFY         no project is touched: Personal works; Projects starts Hermes; a migrated conversation opens with its history; the model picker, memory and skills show;
                   logout ends Projects access. Editing a repository is tested when you add a folder.
ROLLBACK, immediately: hermes-production.py env-off --approved, then the same restart. The old WebUI on {old_port} was never stopped or changed.
"""


def switch(args, on):
    if not args.approved:
        print('Refusing: this changes the production .env. Run it only after the final approval, with --approved.', file=sys.stderr)
        return 2
    try:
        result = production.switch(Path(args.odysseus_root) / '.env', on)
    except production.ProductionError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print(json.dumps(result))
    if result['changed']:
        print('Restart Odysseus for this to take effect. Nothing was restarted.')
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('action', choices=('plan', 'env-on', 'env-off'))
    parser.add_argument('--odysseus-root', default=r'F:\Odysseus')
    parser.add_argument('--target', default=None, help='plan: the branch that would be deployed (default: from config/production-cutover.json)')
    parser.add_argument('--approved', action='store_true', help='env-on / env-off: required; you approved the final migration')
    args = parser.parse_args(argv)
    if args.action == 'plan':
        return plan(args)
    return switch(args, args.action == 'env-on')


if __name__ == '__main__':
    raise SystemExit(main())

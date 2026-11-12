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


def plan(args):
    prod = Path(args.odysseus_root)
    rows = []

    def row(state, name, detail):
        rows.append((state, name, detail))

    code, head = git(prod, 'rev-parse', '--short', 'HEAD')
    _, branch = git(prod, 'rev-parse', '--abbrev-ref', 'HEAD')
    _, dirty = git(prod, 'status', '--porcelain')
    code2, ahead = git(prod, 'rev-list', '--count', f'HEAD..{args.target}')
    ff = git(prod, 'merge-base', '--is-ancestor', 'HEAD', args.target)[0] == 0
    row('OK' if code == 0 and not dirty else 'BLOCK', 'production checkout', f'{branch} @ {head}, {"clean" if not dirty else "HAS UNCOMMITTED CHANGES"}')
    row('TODO' if ff and ahead != '0' else ('OK' if ff else 'BLOCK'), 'code to deploy',
        f'{args.target} is {ahead} commit(s) ahead and a pure fast-forward' if ff else f'{args.target} is NOT a fast-forward of production; merge needed')

    ok, detail = production.verify_wheel(PINS)
    row('OK' if ok else 'BLOCK', 'websockets wheel staged', detail)
    venv_python = prod / 'venv' / 'Scripts' / 'python.exe'
    probe = ('import importlib.metadata as m, sys\n'
             'try: w = m.version("websockets")\nexcept Exception: w = "missing"\n'
             'print(sys.version.split()[0], w)')
    result = subprocess.run([str(venv_python), '-B', '-c', probe], capture_output=True, text=True, timeout=30) if venv_python.is_file() else None
    if result and result.returncode == 0:
        python_version, installed = result.stdout.split()
        pin = json.loads(PINS.read_text(encoding='utf-8'))['websockets']['version']
        row('OK' if installed == pin else 'TODO', 'production venv', f'Python {python_version}; websockets {installed} (pinned {pin})')
    else:
        row('BLOCK', 'production venv', f'{venv_python} could not be run')

    env_file = prod / '.env'
    value = production.current(env_file)
    row('OK', '.env switch', f'ODYSSEUS_HERMES_BACKEND is {"not set (old behaviour)" if value is None else value}')

    root = get_hermes_component_root()
    component = None
    if root:
        try:
            from src.hermes_webapp_component import WebappComponent
            component = WebappComponent(root, application_root=APP_ROOT, environment=dict(os.environ))
            status = dict(component.status())
        except Exception as exc:                                                   # read-only report: never let a check stop the others
            status = {'state': 'unreadable', 'detail': str(exc)[:120]}
        if status.get('state') == 'not_installed':
            row('TODO', 'managed Hermes component', f'not installed under {root}')
        else:
            row('OK' if status.get('route_guard') == 'applied' else 'BLOCK', 'managed Hermes component',
                f"{status.get('state')}, route guard {status.get('route_guard')}, revision {str(status.get('revision'))[:8]}")
            roots = status.get('write_roots')
            row('OK' if roots else 'TODO', 'write roots of the managed gateway', ('; '.join(roots) if roots else 'not set: it would inherit the operator HERMES_WRITE_SAFE_ROOT'))
            logins = status.get('logins') or {}
            row('OK', 'Claude/Codex logins', ', '.join(f'{k}: {v}' for k, v in logins.items()) + ' (isolated unless you allowed them)')
        drive = Path(root).anchor or root
        free = shutil.disk_usage(drive).free
        row('OK' if free > 4 * GIB else 'BLOCK', 'disk for the install', f'{free / GIB:.1f} GB free on {drive} (about 1.3 GB needed)')
    else:
        row('BLOCK', 'managed Hermes component', 'no component root')

    old = http_status(8787)
    row('OK' if old else 'BLOCK', 'old Hermes WebUI (the rollback)', 'answers on 8787' if old else 'NOT answering on 8787: the rollback would not be immediate')
    row('OK' if http_status(7000, '/login') else 'BLOCK', 'Odysseus', 'answers on 7000')
    row('OK', 'port 9130 for the managed gateway', 'free' if http_status(9130) is None else 'in use (fine if it is the managed gateway)')

    width = max(len(name) for _, name, _ in rows)
    print('PRODUCTION READINESS (read-only)\n')
    for state, name, detail in rows:
        print(f'  [{state:5}] {name.ljust(width)}  {detail}')
    blockers = [name for state, name, _ in rows if state == 'BLOCK']
    print(f'\n{len(blockers)} blocker(s)' + (': ' + ', '.join(blockers) if blockers else '') + f'; {sum(1 for r in rows if r[0] == "TODO")} step(s) still to do after approval.')
    print(PROCEDURE.format(prod=prod, target=args.target, wheel=json.loads(PINS.read_text(encoding='utf-8'))['websockets']['staged_in'], head=head,
                           venv=venv_python, app=APP_ROOT))
    return 1 if blockers else 0


PROCEDURE = r'''
PROCEDURE after you approve (every step is reversible; stop after any of them)
  1 backup        python {app}\scripts\odysseus-backup.py create        then   ... verify <name>
                  python {app}\scripts\hermes-migrate.py bundle --source %LOCALAPPDATA%\hermes --out <backups>\hermes-bundle-<date> --python <Hermes python>
  2 dependency    {venv} -m pip install --no-index --find-links {wheel} websockets==<pinned>
                  undo: {venv} -m pip uninstall websockets
  3 code          git -C {prod} merge --ff-only {target}                undo: git -C {prod} reset --hard {head}
  4 release check run the release regression on exactly that commit, compared with the recorded baseline (before any restart)
  5 component     hermes-webapp-component.py ensure --origin managed --python <Hermes python> --home <new home> --port 9130      (about 12 minutes)
                  hermes-webapp-component.py roots --add-root <each approved folder> ; logins --allow claude --allow codex (only if you choose to)
  6 data          hermes-migrate.py plan, apply --confirm-target <new home>, verify          undo: hermes-migrate.py rollback
  7 switch        hermes-production.py env-on --approved ; restart Odysseus through its scheduled task (no active runs)
  8 verify        Personal works; Projects starts Hermes; a migrated conversation, a Git commit, logout
ROLLBACK, immediately: hermes-production.py env-off --approved, then restart Odysseus. The old WebUI on 8787 was never stopped or changed.
'''


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
    parser.add_argument('--target', default='feature/webapp-proxy', help='plan: the branch that would be deployed')
    parser.add_argument('--approved', action='store_true', help='env-on / env-off: required; you approved the final migration')
    args = parser.parse_args(argv)
    if args.action == 'plan':
        return plan(args)
    return switch(args, args.action == 'env-on')


if __name__ == '__main__':
    raise SystemExit(main())

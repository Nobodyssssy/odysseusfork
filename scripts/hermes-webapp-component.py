"""Maintain the optional official Hermes Gateway + Webapp component (separate from the WebUI component in hermes-component.py).

Managed: Odysseus provisions a pinned clone, the official Webapp build, a runtime and a gateway home it owns, and may start/stop it.
Adopted: the user's existing Hermes; read-only. `plan` writes nothing anywhere. Never changes write authorization or approval settings of an adopted home.
"""
import argparse
import json
from pathlib import Path
import sys

APP_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP_ROOT))

from src.hermes_component import ComponentError
from src.hermes_component_runtime import operator_environment
from src.hermes_webapp_component import WebappComponent
from src.runtime_paths import get_hermes_component_root
from src.hermes_webapp_component import LOGIN_WARNING


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('plan', 'ensure', 'repair', 'status', 'start', 'stop', 'restart', 'rollback', 'disable', 'enable', 'blocked', 'roots', 'logins'))
    parser.add_argument('--block', action='append', default=[], help='blocked: add a folder the gateway must never read, write or delete')
    parser.add_argument('--unblock', action='append', default=[], help='blocked: remove a folder from the list')
    parser.add_argument('--add-root', action='append', default=[], help='roots: a folder the managed gateway may write to (its own HERMES_WRITE_SAFE_ROOT)')
    parser.add_argument('--remove-root', action='append', default=[], help='roots: stop allowing a folder')
    parser.add_argument('--clear-roots', action='store_true', help='roots: back to the operator HERMES_WRITE_SAFE_ROOT')
    parser.add_argument('--check-root', action='append', default=[], help='roots: read-only report on a candidate folder (repository root? filesystem? worktree? refused and why); changes nothing')
    parser.add_argument('--allow', action='append', default=[], help='logins: claude or codex; let the gateway use that tool\'s own login (default: isolated)')
    parser.add_argument('--deny', action='append', default=[], help='logins: claude or codex; isolate that login again')
    parser.add_argument('--rebuild', action='store_true', help='ensure/repair: rebuild the release even if it verifies (about 12 minutes)')
    parser.add_argument('--root', type=Path)
    parser.add_argument('--origin', choices=('managed', 'adopted'), default='managed')
    parser.add_argument('--agent', type=Path, help='adopted: the existing Hermes agent checkout')
    parser.add_argument('--home', type=Path, help='the gateway home (adopted: the existing one, never written; managed: created if absent)')
    parser.add_argument('--python', help='Hermes runtime (adopted: required; managed: optional, otherwise one is built)')
    parser.add_argument('--port', type=int, help='Webapp listener port on 127.0.0.1')
    parser.add_argument('--public-url', default='', help='managed: webapp.public_url for the sign-in gate, e.g. http://hermes.localhost:7000')
    parser.add_argument('--repo-root', action='append', default=[], help='managed: a repository discovery root (default: discovery off)')
    parser.add_argument('--no-build', action='store_true', help='adopted: record the install without building a bundle')
    parser.add_argument('--force', action='store_true', help='stop/restart even if sessions look active')
    args = parser.parse_args(argv)
    environment = operator_environment()
    root = args.root or get_hermes_component_root(environment)
    if not root:
        parser.error('An explicit --root is required outside Windows.')
    try:
        component = WebappComponent(root, application_root=APP_ROOT, environment=environment)
        if args.action == 'plan':
            payload = component.plan(args.agent, args.home, args.python, None)
        elif args.action == 'status':
            payload = component.status()
        elif args.action in ('ensure', 'repair', 'enable'):
            payload = component.ensure(args.origin, agent=args.agent, home=args.home, python=args.python, port=args.port, public_url=args.public_url,
                                       repo_roots=args.repo_root, build=not args.no_build, repair=args.action == 'repair', enable=args.action == 'enable',
                                       rebuild=args.rebuild)
            payload = {**payload, 'actions': component.last_actions}
        elif args.action == 'start':
            payload = component.start()
        elif args.action == 'stop':
            payload = component.stop(force=args.force)
        elif args.action == 'restart':
            payload = component.restart(force=args.force)
        elif args.action == 'rollback':
            payload = component.rollback()
        elif args.action == 'blocked':
            if args.block:
                component.set_blocked_folders(args.block)
            for item in args.unblock:
                component.remove_blocked_folder(item)
            payload = {'blocked': component.blocked_folders()}
        elif args.action in ('roots', 'logins'):
            if args.action == 'roots' and args.check_root:
                payload = {'checks': [component.inspect_root(item) for item in args.check_root]}
            elif args.action == 'roots':
                roots = component.set_write_roots(args.add_root, args.remove_root, args.clear_roots) if (args.add_root or args.remove_root or args.clear_roots) else component.write_roots()
                payload = {'write_roots': roots or None, 'inherits_operator_variable': not roots}
            else:
                payload = {'logins': component.set_logins(args.allow, args.deny) if (args.allow or args.deny) else component.logins()}
                payload['warning'] = LOGIN_WARNING
            if (args.add_root or args.remove_root or args.clear_roots or args.allow or args.deny) and component.status().get('state') == 'ready':
                payload['restart_required'] = 'the running gateway keeps its old settings until it is restarted'
        else:
            component.disable()
            payload = {'enabled': False, 'state': 'disabled', 'processes_stopped': False}
        print(json.dumps(payload))
        return 0
    except (ComponentError, OSError, ValueError, RuntimeError) as exc:
        print('Hermes Webapp component: ' + str(exc), file=sys.stderr)
        print('The WebUI component and Normal Mode are unaffected.', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())

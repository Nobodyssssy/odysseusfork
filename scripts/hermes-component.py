"""Maintain the optional local Hermes component. Never changes write authorization."""
import argparse
import json
from pathlib import Path
import sys

APP_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP_ROOT))

from src.application_modes import ModeConfig, ModeService
from src.hermes_component import ComponentError, ComponentManager, read_config
from src.hermes_component_runtime import discover_existing, operator_environment, serve_component
from src.runtime_paths import get_hermes_component_root


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('ensure', 'repair', 'disable', 'enable', 'status', 'serve', 'plan'))
    parser.add_argument('--root', type=Path)
    parser.add_argument('--fresh', action='store_true', help='New isolated install only; never reinitializes saved state')
    parser.add_argument('--webui', type=Path)
    parser.add_argument('--agent', type=Path)
    parser.add_argument('--home', type=Path)
    parser.add_argument('--state-dir', type=Path)
    parser.add_argument('--python')
    parser.add_argument('--url', help='Exact http://127.0.0.1:PORT/ Hermes URL')
    parser.add_argument('--return-url', help='Exact http://127.0.0.1:PORT/ Odysseus URL')
    args = parser.parse_args(argv)
    environment = operator_environment()
    if args.python:
        environment['HERMES_WEBUI_PYTHON'] = args.python
    for option, name in [(args.url, 'ODYSSEUS_HERMES_MODE_URL'), (args.return_url, 'ODYSSEUS_MODE_RETURN_URL')]:
        if option:
            ModeConfig(option)
            environment[name] = option
    root = args.root or get_hermes_component_root(environment)
    if not root:
        parser.error('Automatic Hermes provisioning is Windows-only; an explicit external --root is required elsewhere.')
    try:
        manager = ComponentManager(root, application_root=APP_ROOT, environment=environment)
        config = read_config(manager.config_path)
        if args.action == 'status':
            if not config:
                payload = {'enabled': False, 'state': 'not_installed'}
            elif not config['enabled']:
                payload = {'enabled': False, 'state': 'disabled'}
            else:
                manager.validate(config)
                payload = ModeService(ModeConfig(config['url'])).status()
        elif args.action == 'disable':
            manager.disable()
            payload = {'enabled': False, 'state': 'disabled', 'processes_stopped': False}
        elif args.action == 'serve':
            return serve_component(root, APP_ROOT, environment)
        else:
            existing = None
            if (not config or args.action == 'plan') and not args.fresh:
                existing = discover_existing(APP_ROOT, environment, args.webui, args.agent, args.home, args.python, args.state_dir)
            if args.action == 'plan':
                payload = {'existing': existing, 'writes_performed': False}
            else:
                payload = manager.ensure(repair=args.action == 'repair', fresh=args.fresh, existing=existing, enable=args.action == 'enable')
        print(json.dumps(payload))
        return 0
    except (ComponentError, OSError, ValueError) as exc:
        print('Hermes component: ' + str(exc), file=sys.stderr)
        print('Normal Mode can still run. Use -NoHermes to skip setup, or run this script with repair after resolving the reported problem.', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())

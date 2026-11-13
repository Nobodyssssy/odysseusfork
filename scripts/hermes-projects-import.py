"""Bring legacy WebUI projects into the Hermes Webapp's project store. Dry run unless --apply. See src/hermes_projects_import.py for what it never does.

    python scripts/hermes-projects-import.py [--root COMPONENT_ROOT] [--webui-state DIR] [--apply]
"""
import argparse
import json
import os
from pathlib import Path
import sys

APP_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP_ROOT))

from src import hermes_projects_import as importer
from src.runtime_paths import get_hermes_component_root
from src.hermes_webapp_component import WebappComponent


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    parser.add_argument('--root', default=None, help='Hermes component root (default: the Odysseus one)')
    parser.add_argument('--webui-state', default=None, help='legacy WebUI state directory (default: %LOCALAPPDATA%\\hermes\\webui)')
    parser.add_argument('--apply', action='store_true', help='create the projects (default: only print the plan)')
    args = parser.parse_args(argv)
    root = Path(args.root) if args.root else get_hermes_component_root()
    webui = args.webui_state or os.environ.get('ODYSSEUS_WEBUI_STATE') or str(Path(os.environ.get('LOCALAPPDATA', Path.home() / 'AppData/Local')) / 'hermes' / 'webui')
    config = WebappComponent(root, application_root=APP_ROOT).config()
    rpc = importer.GatewayRpc(config['url'], importer.token_for(config['home']))
    existing = (rpc.call('projects.list') or {}).get('projects', [])
    actions = importer.plan(importer.legacy_projects(webui), existing)
    for a in actions:
        print(f"{a['action']:6} {a['name']:<22} {a['folder'] or '-':<55} {a['conversations']} conversation(s) {a['reason']}")
    if not args.apply:
        print('Dry run: nothing was changed. Re-run with --apply to create the projects marked "create".')
        return 0
    results = importer.execute(actions, rpc)
    for name, outcome in results:
        print(f'{name}: {outcome}')
    return 0 if all(o == 'created' for _, o in results) else 1


if __name__ == '__main__':
    sys.exit(main())

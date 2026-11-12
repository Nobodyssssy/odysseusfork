"""Migrate an existing Hermes home into the managed Webapp gateway home (see docs/hermes-migration.md).

    inventory --source HOME                  read-only: what is there and what would happen to each part
    bundle    --source HOME --out DIR        read-only on HOME: build a migration bundle (credentials are NOT included unless named)
    plan      --bundle DIR --root ROOT       read-only: preflight and configuration preview against the managed home
    apply     --bundle DIR --root ROOT --confirm-target PATH   write into the managed home (gateway stopped, home empty)
    verify    --bundle DIR --root ROOT       read-only: compare the managed home with the bundle
    rollback  --root ROOT --safety-copy ZIP --confirm-target PATH   put the managed home back as it was

The target is always the managed component's own home (from ROOT); there is no way to point apply at another folder.
"""
import argparse
import json
from pathlib import Path
import sys

APP_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP_ROOT))

from src import hermes_migration as migration
from src.hermes_component import ComponentError
from src.hermes_component_runtime import operator_environment
from src.hermes_webapp_component import WebappComponent
from src.runtime_paths import get_hermes_component_root


def _managed(root, environment):
    component = WebappComponent(root, application_root=APP_ROOT, environment=environment)
    config = component.config()
    if not config or config.get('origin') != 'managed':
        raise migration.MigrationError('The Webapp component is not installed as MANAGED here; the migration only writes into a managed home.')
    return component, config


def _markdown(report):
    lines = [f"# Hermes home inventory: {report['source']}", '', '| Item | Kind | Size | Files | What a migration does |', '|---|---|---:|---:|---|']
    for item in report['items']:
        size = 'n/a' if item['bytes'] is None else f"{item['bytes'] / 1e6:.1f} MB" + ('+' if item['size_truncated'] else '')
        lines.append(f"| `{item['name']}` | {item['kind']} | {size} | {item['files'] if item['files'] is not None else 'n/a'} | **{item['category']}**: {item['reason']} |")
    for key in ('env', 'auth_json', 'databases', 'webui_projects', 'profiles', 'cron_jobs', 'plugins'):
        if key in report:
            lines += ['', f'**{key}**: `{json.dumps(report[key])}`']
    return '\n'.join(lines)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('action', choices=('inventory', 'bundle', 'plan', 'apply', 'verify', 'rollback'))
    parser.add_argument('--source', type=Path)
    parser.add_argument('--out', type=Path)
    parser.add_argument('--bundle', type=Path)
    parser.add_argument('--root', type=Path)
    parser.add_argument('--python', help='the Hermes interpreter (bundle: required; others: taken from the managed install)')
    parser.add_argument('--carry-credential', action='append', default=[], help='bundle: carry this one .env credential (default: none)')
    parser.add_argument('--confirm-target', type=Path)
    parser.add_argument('--safety-copy', type=Path)
    parser.add_argument('--markdown', action='store_true')
    args = parser.parse_args(argv)
    environment = operator_environment()
    try:
        if args.action == 'inventory':
            report = migration.inventory(args.source)
            print(_markdown(report) if args.markdown else json.dumps(report, indent=1))
            return 0
        if args.action == 'bundle':
            if not (args.source and args.out and args.python):
                parser.error('bundle needs --source, --out and --python')
            manifest = migration.build_bundle(args.source, args.out, args.python, args.carry_credential)
            print(json.dumps({k: manifest[k] for k in ('entries', 'databases', 'env', 'plugins', 'blocked_folders', 'left_out', 'data_zip_sha256')}, indent=1))
            return 0
        root = args.root or get_hermes_component_root(environment)
        if not root or not args.bundle and args.action != 'rollback':
            parser.error('this action needs --bundle and a component root')
        component, config = _managed(root, environment)
        home = Path(config['home'])
        if args.action in ('apply', 'rollback'):
            if not args.confirm_target or Path(args.confirm_target).resolve() != home.resolve():
                raise migration.MigrationError(f'Pass --confirm-target {home} to confirm which home will be written.')
            if component.status()['state'] != 'stopped':
                raise migration.MigrationError('The managed gateway is not stopped; stop it first (Services > Stop Hermes).')
        if args.action == 'plan':
            problems = migration.preflight(args.bundle, home)
            preview = migration._run_python(config['python'], migration._MERGE_CONFIG, Path(args.bundle) / 'config.filtered.yaml', home / 'config.yaml', '0', config['agent'])
            print(json.dumps({'target': str(home), 'problems': problems, 'config_preview': json.loads(preview.stdout.strip().splitlines()[-1]) if preview.returncode == 0 else preview.stderr[-300:],
                              'manifest': {k: v for k, v in migration.read_manifest(args.bundle).items() if k not in ('entry_sha256',)}}, indent=1))
            return 1 if problems else 0
        if args.action == 'apply':
            record = migration.apply_bundle(args.bundle, home, config['python'], config['agent'], Path(root) / 'migration' / 'backups')
            print(json.dumps(record, indent=1))
            return 0
        if args.action == 'verify':
            result = migration.verify(args.bundle, home)
            print(json.dumps(result, indent=1))
            return 0 if result['ok'] else 1
        if args.action == 'rollback':
            print(json.dumps({'moved_aside': migration.rollback(home, args.safety_copy)}))
            return 0
    except (migration.MigrationError, ComponentError, OSError, ValueError) as exc:
        print('Hermes migration: ' + str(exc), file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())

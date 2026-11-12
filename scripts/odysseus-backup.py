#!/usr/bin/env python3
"""Full backup and offline restore for Odysseus and its linked Hermes.

  create                      verified snapshot of Odysseus data, Hermes state, startup files and tasks
  list                        snapshots in the backup folder
  verify <name|path>          re-hash a snapshot
  restore <name|path>         DRY RUN: what would change.   Add --apply to do it.

Restore is offline on purpose: stop Odysseus, Hermes, the Hermes gateway and Chroma first. It snapshots the
current state before overwriting, restores files that are in the snapshot, and never deletes anything else.
Scheduled tasks are only re-registered with --restore-tasks. The HERMES_WRITE_SAFE_ROOT variable is shown,
never set: it is a security setting that only you should change.

Works from a fresh checkout, so it can restore data right after a reinstall.
"""
import argparse
import json
import os
from pathlib import Path
import sys
from urllib.parse import urlsplit

APP_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP_ROOT))

from src import hermes_backup as backup  # noqa: E402
from src.hermes_component import read_config  # noqa: E402


def human(size):
    for unit in ('B', 'KB', 'MB', 'GB'):
        if size < 1024 or unit == 'GB':
            return f'{size:.0f} {unit}' if unit == 'B' else f'{size:.1f} {unit}'
        size /= 1024


def resolve(args):
    roots = backup.default_roots()
    for key, value in (('backup_dir', args.backup_dir), ('odysseus', args.odysseus_root), ('hermes', args.hermes_home),
                       ('component', args.component_root), ('startup', args.startup_dir), ('data', args.data_dir)):
        if value:
            roots[key] = Path(value)
    return roots


def snapshot_from(roots, value):
    path = Path(value)
    return path if path.is_dir() and (path / 'manifest.json').is_file() else backup.snapshot_path(roots['backup_dir'], value)


def service_ports(roots):
    ports = [int(os.environ.get('APP_PORT') or 7000), int(os.environ.get('CHROMADB_PORT') or 8100)]
    try:
        saved = read_config(Path(roots['component']) / 'managed' / 'component.json') if roots.get('component') else None
        ports.append(urlsplit(saved['url']).port if saved else 8787)
    except Exception:
        ports.append(8787)
    return sorted(set(port for port in ports if port))


def cmd_create(args, roots):
    destination, report = backup.create_full_backup(roots=roots, exclude_secrets=args.exclude_secrets)
    print(f'Snapshot: {destination}')
    print(f'Verified: {report["files"]} files, {human(report["bytes"])}, {report["sqlite_backups"]} databases')
    if args.exclude_secrets:
        manifest = json.loads((destination / 'manifest.json').read_text(encoding='utf-8'))['secrets']
        for name in manifest['too_large_to_scan_not_copied']:
            print(f'  not copied (too large to check for secrets): {name}')
        print(f'Secrets excluded: {len(manifest["files_not_copied"])} file(s) not copied, values removed from {len(manifest["values_removed"])} file(s). '
              'Conversations, memories and other content are copied as is. After a restore you must sign in / enter keys again.')
    return 0


def cmd_list(args, roots):
    rows = backup.list_backups(roots['backup_dir'])
    print(f'Backup folder: {roots["backup_dir"]}')
    for row in rows:
        print(f'  {row["name"]}  {row["created_at"] or "?"}  {row["files"]} files  {human(row["bytes"])}  '
              f'{"Odysseus+Hermes" if row["hermes"] else "Odysseus only"}  tasks: {", ".join(row["tasks"]) or "none"}'
              + ('  [secrets excluded]' if row.get('secrets_excluded') else ''))
    if not rows:
        print('  (none)')
    return 0


def cmd_verify(args, roots):
    report = backup.verify_backup(snapshot_from(roots, args.snapshot))
    print(f'OK: {report["files"]} files, {human(report["bytes"])}, {report["sqlite_backups"]} databases verified')
    return 0


def cmd_restore(args, roots):
    snapshot = snapshot_from(roots, args.snapshot)
    busy = [] if args.force_running else backup.running_services(roots.get('hermes'), service_ports(roots))
    result = backup.restore_backup(
        snapshot, roots['odysseus'], roots.get('hermes'), roots.get('component'), roots.get('startup'), data_dir=roots.get('data'),
        apply=args.apply, safety_backup_root=False if args.skip_safety_backup else roots['backup_dir'],
        busy=busy, restore_tasks=args.restore_tasks, overwrite_redacted=args.overwrite_redacted)
    print(f'Snapshot {Path(result["snapshot"]).name} ({result["created_at"]}): {result["files"]} files, {human(result["bytes"])}')
    print(f'  new {result["new"]}, identical {result["same"]}, would overwrite {result["overwrite"]}, databases {result["sqlite"]}')
    for path in result['overwrites'][:10]:
        print(f'    overwrites {path}')
    if result.get('secrets_excluded'):
        print(f'  THIS SNAPSHOT HAS NO SECRETS. {len(result["kept_live_secrets"])} existing file(s) are left as they are so their secrets are not erased'
              + (':' if result['kept_live_secrets'] else '.') + '  (--overwrite-redacted restores them anyway)')
        for path in result['kept_live_secrets'][:10]:
            print(f'    kept {path}')
        print('  After restoring you must sign in again and re-enter keys: Hermes providers and API keys, Odysseus secrets, email passwords, search keys.')
    for item in result['skipped'][:10]:
        print(f'  skipped {item["path"]}: {item["reason"]}')
    if result['tasks']:
        print(f'  scheduled tasks in snapshot: {", ".join(result["tasks"])}' + ('' if args.restore_tasks else '  (add --restore-tasks to re-register)'))
    safe_root = result['environment'].get('HERMES_WRITE_SAFE_ROOT')
    if safe_root:
        print('  Windows user variable to set yourself if missing (Hermes folder permissions):')
        print(f'    [Environment]::SetEnvironmentVariable("HERMES_WRITE_SAFE_ROOT", "{safe_root}", "User")')
    if not result['applied']:
        print('\nDry run only: nothing was changed. Stop Odysseus, Hermes, the Hermes gateway and Chroma, then add --apply.')
        if busy:
            print('Still running: ' + ', '.join(busy))
        return 0
    if result['safety_backup']:
        print(f'  safety copy of the previous state: {result["safety_backup"]}')
    print(f'Restored {result["restored"]} files.')
    for name in result['tasks_restored']:
        print(f'  task registered: {name}')
    for name, why in result['tasks_failed'].items():
        print(f'  task NOT registered: {name}: {why}')
    for item in result['failed']:
        print(f'  FAILED {item["path"]}: {item["error"]}')
    return 1 if result['failed'] or result['tasks_failed'] else 0


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    for flag in ('backup-dir', 'odysseus-root', 'hermes-home', 'component-root', 'startup-dir', 'data-dir'):
        parser.add_argument('--' + flag, help='override the detected folder')
    sub = parser.add_subparsers(dest='command', required=True)
    create = sub.add_parser('create')
    create.add_argument('--exclude-secrets', action='store_true', help='do not copy credential files; remove secret values from the copies (the live files are never changed)')
    sub.add_parser('list')
    sub.add_parser('verify').add_argument('snapshot')
    restore = sub.add_parser('restore')
    restore.add_argument('snapshot')
    restore.add_argument('--apply', action='store_true', help='actually restore (default is a dry run)')
    restore.add_argument('--overwrite-redacted', action='store_true', help='allow a secrets-excluded snapshot to overwrite live files that hold secrets (they would lose them)')
    restore.add_argument('--restore-tasks', action='store_true', help='re-register the saved Windows scheduled tasks')
    restore.add_argument('--skip-safety-backup', action='store_true', help='do not snapshot the current state first (not recommended)')
    restore.add_argument('--force-running', action='store_true', help='skip the "is anything still running" check (can corrupt databases)')
    args = parser.parse_args(argv)
    roots = resolve(args)
    try:
        return {'create': cmd_create, 'list': cmd_list, 'verify': cmd_verify, 'restore': cmd_restore}[args.command](args, roots)
    except backup.RestoreBlocked as exc:
        print(f'Refused: {exc}. Nothing was changed.')
        return 2
    except (ValueError, OSError, json.JSONDecodeError) as exc:
        print(f'Error: {exc}')
        return 1


if __name__ == '__main__':
    sys.exit(main())

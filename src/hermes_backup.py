"""Private verified snapshots. No restore is automatic; running apps are not paused."""
from contextlib import closing
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import shutil
import sqlite3
import uuid

from src.hermes_component import atomic_json, private_directory, safe_directory

_SQLITE_HEADER = b'SQLite format 3\x00'
_WEIGHT_SUFFIXES = {'.gguf', '.safetensors', '.onnx', '.pt', '.pth'}


def _digest(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def _snapshot_file(source, destination):
    destination.parent.mkdir(parents=True, exist_ok=True)
    with source.open('rb') as stream:
        sqlite = stream.read(16) == _SQLITE_HEADER
    if sqlite:
        with closing(sqlite3.connect(source.resolve().as_uri() + '?mode=ro', uri=True, timeout=15)) as original:
            with closing(sqlite3.connect(destination)) as backup:
                original.backup(backup, pages=256, sleep=0.05)
                if backup.execute('pragma integrity_check').fetchone()[0] != 'ok':
                    raise ValueError('SQLite backup failed its integrity check')
        return 'sqlite-online'
    for _ in range(5):
        before = source.stat()
        shutil.copy2(source, destination)
        after = source.stat()
        if (before.st_size, before.st_mtime_ns) == (after.st_size, after.st_mtime_ns) and _digest(source) == _digest(destination):
            return 'stable-copy'
    raise ValueError('A backup source did not stabilize; no complete backup was declared')


def create_backup(odysseus_root, hermes_home, backup_root, startup_files=(), task_xml=None):
    ody, hermes, base = (safe_directory(path) for path in (odysseus_root, hermes_home, backup_root))
    if base.is_relative_to(ody) or base.is_relative_to(hermes):
        raise ValueError('Backups must be outside application source and Hermes state trees')
    destination = private_directory(base / ('snapshot-' + datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S') + '-' + uuid.uuid4().hex[:6]))
    entries, excluded = [], []

    def copy(source, relative):
        if source.is_symlink() or (hasattr(source, 'is_junction') and source.is_junction()):
            raise ValueError('Backup encountered a reparse point; review it before proceeding')
        target = destination / relative
        method = _snapshot_file(source, target)
        entries.append({'source': str(source), 'relative_path': relative.as_posix(),
                        'bytes': target.stat().st_size, 'sha256': _digest(target), 'method': method})

    def tree(source, relative, root_exclusions=()):
        if not source.exists():
            return
        for path in source.iterdir():
            if path.name in root_exclusions or path.suffix.lower() in _WEIGHT_SUFFIXES:
                excluded.append(str(path))
                continue
            if path.name.endswith(('-wal', '-shm', '-journal')):
                continue
            if path.is_symlink() or (hasattr(path, 'is_junction') and path.is_junction()):
                raise ValueError('Backup encountered a reparse point; no complete backup was declared')
            if path.is_dir():
                tree(path, relative / path.name)
            elif path.is_file():
                copy(path, relative / path.name)

    tree(ody / 'data', Path('odysseus/data'), ('fastembed_cache', 'logs'))
    for name in ('.env', 'launch-windows.ps1', 'launcher.py'):
        if (ody / name).is_file():
            copy(ody / name, Path('odysseus') / name)
    tree(hermes, Path('hermes'), ('hermes-agent', 'tools', 'installs', 'cache', 'lsp', 'logs'))
    for source in startup_files:
        source = Path(source)
        if source.is_file():
            copy(source, Path('startup') / source.name)
    if task_xml is not None:
        target = destination / 'startup/task.xml'
        target.parent.mkdir(exist_ok=True)
        target.write_text(task_xml, encoding='utf-8')
        entries.append({'source': 'Windows Task Scheduler export', 'relative_path': 'startup/task.xml',
                        'bytes': target.stat().st_size, 'sha256': _digest(target), 'method': 'task-export'})
    atomic_json(destination / 'manifest.json', {
        'version': 1, 'created_at': datetime.now(timezone.utc).isoformat(), 'entries': entries, 'excluded': excluded,
        'consistency': 'Each SQLite database is transactionally consistent; static files are individually stabilized. Not an atomic cross-application snapshot.',
        'scope': 'Odysseus data/config/startup plus Hermes user state, sessions, memories, skills and config. Runtime installs, caches, logs and large model-weight formats are excluded.',
    })
    verify_backup(destination)
    return destination


def verify_backup(destination):
    destination = safe_directory(destination)
    manifest = json.loads((destination / 'manifest.json').read_text(encoding='utf-8'))
    total, sqlite_count = 0, 0
    for item in manifest['entries']:
        relative = Path(item['relative_path'])
        target = (destination / relative).resolve()
        if not target.is_relative_to(destination) or not target.is_file() or _digest(target) != item['sha256']:
            raise ValueError('Backup file missing, outside its root or hash verification failed')
        total += target.stat().st_size
        if item['method'] == 'sqlite-online':
            with closing(sqlite3.connect(target.as_uri() + '?mode=ro', uri=True)) as database:
                if database.execute('pragma integrity_check').fetchone()[0] != 'ok':
                    raise ValueError('Backup SQLite integrity check failed')
            sqlite_count += 1
    return {'verified': True, 'files': len(manifest['entries']), 'bytes': total, 'sqlite_backups': sqlite_count, 'backup': str(destination)}

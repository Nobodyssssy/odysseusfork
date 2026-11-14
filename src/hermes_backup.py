"""Private verified snapshots of Odysseus and Hermes, plus an offline, non-destructive restore.

A snapshot is a folder holding ``manifest.json`` and verified copies of the files. Taking one never pauses
a running app (SQLite databases are copied with the online backup API). Restoring is deliberately offline:
Odysseus, Hermes, the Hermes gateway and Chroma must be stopped, a dry run is the default, the current state
is snapshotted first, and nothing that is not in the snapshot is ever deleted.
"""
from contextlib import closing
from datetime import datetime, timezone
import hashlib
import fnmatch
import json
import os
from pathlib import Path
import re
import shutil
import socket
import sqlite3
import subprocess
import tempfile
import uuid

from src.hermes_component import ComponentError, atomic_json, private_directory, read_config, safe_directory

_SQLITE_HEADER = b'SQLite format 3\x00'
_WEIGHT_SUFFIXES = {'.gguf', '.safetensors', '.onnx', '.pt', '.pth'}
_SNAPSHOT_NAME = re.compile(r'^(snapshot|prerestore)-\d{8}-\d{6}-[0-9a-f]{6}$')
_TASK_NAME = re.compile(r'^[A-Za-z0-9 _.\-]{1,100}$')
_ENVIRONMENT_KEYS = ('HERMES_WRITE_SAFE_ROOT', 'HERMES_HOME', 'HERMES_WEBUI_STATE_DIR', 'ODYSSEUS_DATA_DIR',
                     'ODYSSEUS_RESTART_TASK', 'ODYSSEUS_HERMES_COMPONENT_ROOT', 'ODYSSEUS_BACKUP_DIR')
_HERMES_EXCLUDED = ('hermes-agent', 'tools', 'installs', 'cache', 'lsp', 'logs')
_MAX_STARTUP_FILE = 1024 * 1024
MANIFEST_VERSION = 2

# -- --exclude-secrets ------------------------------------------------------------------------------------
# Applied to the COPY in the backup folder only; the live files are never modified. Not a promise that nothing sensitive remains: conversation text,
# memories, notes and project data are copied as they are (see docs/hermes-backups-and-secrets.md).
_SECRET_FILE_NAMES = {'auth.json', '.signing_key', '.app_key', '.pbkdf2_key', 'vault.key', 'vault.json.enc', 'session-token', '.sessions.json', 'sessions.json',
                      '.credentials.json', 'id_rsa', 'id_ed25519'}
_SECRET_FILE_PATTERNS = ('auth.json.*', '*.pem', '*.key', '*.p12', '*.pfx', 'credentials*.json', 'id_rsa.*', 'id_ed25519.*')
_SECRET_ENV_NAME = re.compile(r'(KEY|TOKEN|SECRET|PASS|CREDENTIAL|COOKIE|PRIVATE|SALT|HASH|AUTH|SIGNING)', re.I)
_SECRET_KEY = re.compile(r'(api_?key|passw(or)?d|passwd|secret|credential|private_?key|(access|refresh|oauth|webhook|auth|bearer|session|id|api)_?tokens?$|token_?hash|oauth_?tokens)', re.I)
_JSON_SCRUB_LIMIT = 128 * 1024 * 1024        # larger JSON files are parsed in memory; the old WebUI's conversation files are tens of MB
_YAML_INLINE = re.compile(r'^\s*[\w.\-]*(api_?key|token|secret|passw(or)?d|credential)[\w.\-]*\s*:\s*(?!["\']?(\$\{|\s*$|null\b|~|false\b|true\b|none\b))["\']?[^\s"\'#][^\n]{5,}', re.I | re.M)


class RestoreBlocked(RuntimeError):
    """A restore was refused because something that uses the files is still running."""


def _is_secret_file(name):
    lowered = name.lower()
    return lowered in _SECRET_FILE_NAMES or any(fnmatch.fnmatch(lowered, pattern) for pattern in _SECRET_FILE_PATTERNS)


def _redact_env(target):
    """Keep the lines whose variable name is not credential-like. Returns the names removed."""
    kept, removed = [], []
    for line in target.read_text(encoding='utf-8-sig', errors='replace').splitlines():
        text = line.strip()
        if text and not text.startswith('#') and '=' in text and _SECRET_ENV_NAME.search(text.split('=', 1)[0].replace('export', '').strip()):
            removed.append(text.split('=', 1)[0].strip())
            continue
        kept.append(line)
    target.write_text('\n'.join(kept) + '\n', encoding='utf-8', newline='\n')
    return removed


def _scrub_json(value, path='', removed=None):
    removed = [] if removed is None else removed
    if isinstance(value, dict):
        for key, item in list(value.items()):
            where = f'{path}.{key}' if path else str(key)
            if isinstance(item, str) and item and _SECRET_KEY.search(str(key)):
                value[key] = ''
                removed.append(where)
            else:
                _scrub_json(item, where, removed)
    elif isinstance(value, list):
        for item in value:
            _scrub_json(item, path + '[]', removed)
    return removed


class _TooLargeToScan(Exception):
    pass


def _redact_json_file(target):
    if target.stat().st_size > _JSON_SCRUB_LIMIT:
        raise _TooLargeToScan()
    try:
        data = json.loads(target.read_text(encoding='utf-8-sig'))
    except ValueError:
        return []
    removed = _scrub_json(data)
    if removed:
        target.write_text(json.dumps(data, indent=1), encoding='utf-8')
    return removed


def _redact_sqlite(target):
    """Blank secret-looking TEXT/BLOB columns in the backup copy, then VACUUM so the old values do not linger in free pages."""
    scrubbed = []
    with closing(sqlite3.connect(str(target))) as database:
        database.execute('pragma secure_delete=ON')
        for (table,) in database.execute("select name from sqlite_master where type='table' and name not like 'sqlite_%'").fetchall():
            if '_fts' in table:
                continue
            for _cid, column, kind, *_rest in database.execute(f'pragma table_info("{table}")').fetchall():
                if _SECRET_KEY.search(column) and not re.search(r'INT|REAL|NUM|FLOAT|DOUBLE|BOOL', (kind or '').upper()):
                    changed = database.execute(f'update "{table}" set "{column}" = case when typeof("{column}")=\'blob\' then x\'\' else \'\' end '
                                               f'where "{column}" is not null and "{column}" != \'\'').rowcount
                    if changed:
                        scrubbed.append(f'{table}.{column} ({changed})')
        database.commit()
        database.execute('pragma journal_mode=DELETE')
        database.execute('vacuum')
    for suffix in ('-wal', '-shm', '-journal'):
        Path(str(target) + suffix).unlink(missing_ok=True)
    return scrubbed


def _digest(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def _is_reparse(path):
    return path.is_symlink() or (hasattr(path, 'is_junction') and path.is_junction())


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


# ── Windows scheduled tasks (the startup that a reinstall would otherwise lose) ──────────────
def _powershell(script, variables, timeout=30):
    return subprocess.run(['powershell.exe', '-NoProfile', '-NonInteractive', '-Command', script], capture_output=True,
                          timeout=timeout, env={**os.environ, **variables}, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))


def export_task(name):
    """The scheduled task's XML, or None when it does not exist or this is not Windows."""
    if os.name != 'nt' or not _TASK_NAME.match(name):
        return None
    script = ("[Console]::OutputEncoding = [Text.Encoding]::UTF8; "
              "if (-not (Get-ScheduledTask -TaskName $env:ODY_TASK -ErrorAction SilentlyContinue)) { exit 3 }; "
              "Export-ScheduledTask -TaskName $env:ODY_TASK")
    try:
        result = _powershell(script, {'ODY_TASK': name})
    except (OSError, subprocess.SubprocessError):
        return None
    text = result.stdout.decode('utf-8-sig', errors='replace').strip() if result.returncode == 0 else ''
    return text if text.startswith(('<?xml', '<Task')) else None


def import_task(name, xml):
    """Register (or replace) the named task from exported XML. Raises ValueError with Windows' reason on failure."""
    if os.name != 'nt' or not _TASK_NAME.match(name):
        raise ValueError('Scheduled tasks can only be restored on Windows, with a plain task name.')
    handle = tempfile.NamedTemporaryFile('w', suffix='.xml', delete=False, encoding='utf-8')
    try:
        with handle:
            handle.write(xml)
        script = ("$x = Get-Content -LiteralPath $env:ODY_XML -Raw -Encoding UTF8; "
                  "Register-ScheduledTask -TaskName $env:ODY_TASK -Xml $x -Force | Out-Null")
        result = _powershell(script, {'ODY_TASK': name, 'ODY_XML': handle.name})
        if result.returncode:
            raise ValueError(result.stderr.decode('utf-8', errors='replace').strip()[:400] or 'Windows refused the task')
    finally:
        Path(handle.name).unlink(missing_ok=True)


# ── Where things live ───────────────────────────────────────────────────────────────────────
def default_roots(environment=None):
    """Resolve the folders a full backup covers, from this installation and its environment."""
    from src.runtime_paths import get_app_root, get_default_data_dir, get_hermes_component_root
    env = dict(os.environ if environment is None else environment)
    odysseus = Path(get_app_root())
    component = get_hermes_component_root(env)
    component = Path(component) if component else None
    home = None
    if component:
        try:
            home = (read_config(component / 'managed' / 'component.json') or {}).get('home')
        except ComponentError:
            home = None
    home = home or env.get('HERMES_HOME')
    local = env.get('LOCALAPPDATA')
    if not home and local and (Path(local) / 'hermes').is_dir():
        home = str(Path(local) / 'hermes')
    return {
        'odysseus': odysseus,
        'data': Path(env.get('ODYSSEUS_DATA_DIR') or get_default_data_dir()),
        'hermes': Path(home) if home else None,
        'component': component,
        'startup': Path(local) / 'OdysseusStartup' if local else None,
        'backup_dir': Path(env.get('ODYSSEUS_BACKUP_DIR') or odysseus.parent / 'Odysseus-Backups'),
    }


# ── Create ──────────────────────────────────────────────────────────────────────────────────
def _build_backup(odysseus_root, hermes_home, backup_root, startup_files=(), task_xml=None, *, component_root=None,
                  task_xmls=None, data_dir=None, environment=None, label='snapshot', exclude_secrets=False):
    ody = safe_directory(odysseus_root)
    hermes = safe_directory(hermes_home) if hermes_home else None
    component = safe_directory(component_root) if component_root else None
    data = safe_directory(data_dir) if data_dir else ody / 'data'
    base = safe_directory(backup_root)
    if any(base.is_relative_to(path) for path in (ody, hermes, component, data) if path):
        raise ValueError('Backups must be outside application source, data and Hermes state trees')
    stamp = datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')
    destination = private_directory(base / f'{label}-{stamp}-{uuid.uuid4().hex[:6]}')
    entries, excluded, redacted, secret_files, unscanned = [], [], [], [], []

    def copy(source, relative):
        if _is_reparse(source):
            raise ValueError('Backup encountered a reparse point; review it before proceeding')
        if exclude_secrets and _is_secret_file(source.name):
            secret_files.append(str(source))
            return
        target = destination / relative
        method = _snapshot_file(source, target)
        entry = {'source': str(source), 'relative_path': relative.as_posix(), 'method': method}
        if exclude_secrets:
            removed, name = [], source.name.lower()
            if name == '.env' or name.startswith('.env.'):
                removed = _redact_env(target)
            elif method == 'sqlite-online':
                removed = _redact_sqlite(target)
            elif name.endswith('.json'):
                try:
                    removed = _redact_json_file(target)
                except _TooLargeToScan:         # cannot be checked for secrets, so it is not kept (usually a regenerable cache); the manifest says so
                    target.unlink(missing_ok=True)
                    unscanned.append(relative.as_posix())
                    return
            elif name.endswith(('.yaml', '.yml')):
                if _YAML_INLINE.search(target.read_text(encoding='utf-8-sig', errors='replace')):
                    target.unlink(missing_ok=True)
                    raise ValueError(f'{source} holds an inline secret-like value; remove it or back up without --exclude-secrets. Nothing was declared complete.')
            if removed:
                entry['redacted'] = True
                redacted.append({'path': relative.as_posix(), 'removed': removed})
        entry.update({'bytes': target.stat().st_size, 'sha256': _digest(target)})
        entries.append(entry)

    def tree(source, relative, root_exclusions=()):
        if not source.exists():
            return
        for path in source.iterdir():
            if path.name in root_exclusions or path.suffix.lower() in _WEIGHT_SUFFIXES:
                excluded.append(str(path))
                continue
            if path.name.endswith(('-wal', '-shm', '-journal')):
                continue
            if path.name.startswith('hermes-grants.db'):          # short-lived access grants: a restore must never revive one
                excluded.append(str(path))
                continue
            if _is_reparse(path):
                raise ValueError('Backup encountered a reparse point; no complete backup was declared')
            if path.is_dir():
                tree(path, relative / path.name)
            elif path.is_file():
                copy(path, relative / path.name)

    def text_entry(relative, text, method='task-export'):
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding='utf-8')
        entries.append({'source': 'Windows Task Scheduler export', 'relative_path': relative.as_posix(),
                        'bytes': target.stat().st_size, 'sha256': _digest(target), 'method': method})

    tree(data, Path('odysseus/data'), ('fastembed_cache', 'logs'))
    for name in ('.env', 'launch-windows.ps1', 'launcher.py'):
        if (ody / name).is_file():
            copy(ody / name, Path('odysseus') / name)
    if hermes:
        tree(hermes, Path('hermes'), _HERMES_EXCLUDED)
    if component and (component / 'managed').is_dir():
        for path in sorted((component / 'managed').glob('*.json')):
            if path.is_file():
                copy(path, Path('component/managed') / path.name)
    if component and (component / 'managed' / 'webapp' / 'component.json').is_file():
        copy(component / 'managed' / 'webapp' / 'component.json', Path('component/managed/webapp/component.json'))
    if component and (component / 'state' / 'hermes-gateway').is_dir():       # the Webapp gateway's own sessions and settings; its sign-in credential is regenerated, not saved
        tree(component / 'state' / 'hermes-gateway', Path('component/state/hermes-gateway'), ('webapp', 'logs', 'cache', 'tmp') + _HERMES_EXCLUDED)
    for source in startup_files:
        source = Path(source)
        if source.is_file():
            copy(source, Path('startup') / source.name)
    if task_xml is not None:
        text_entry(Path('startup/task.xml'), task_xml)
    tasks = {}
    for name, xml in (task_xmls or {}).items():
        if not _TASK_NAME.match(name):
            raise ValueError('Scheduled task names must be plain names.')
        relative = Path('startup/tasks') / (re.sub(r'[^A-Za-z0-9._-]', '_', name) + '.xml')
        text_entry(relative, xml)
        tasks[name] = relative.as_posix()
    env = os.environ if environment is None else environment
    atomic_json(destination / 'manifest.json', {
        'version': MANIFEST_VERSION, 'created_at': datetime.now(timezone.utc).isoformat(), 'entries': entries, 'excluded': excluded,
        'roots': {'odysseus': str(ody), 'data': str(data), 'hermes': str(hermes) if hermes else None,
                  'component': str(component) if component else None},
        'tasks': tasks,
        'environment': {key: env[key] for key in _ENVIRONMENT_KEYS if env.get(key)},
        'consistency': 'Each SQLite database is transactionally consistent; static files are individually stabilized. Not an atomic cross-application snapshot.',
        'scope': ('Odysseus data/config/startup plus Hermes user state, sessions, memories, skills, plugins and config, the Odysseus-Hermes component settings and the Windows startup tasks. Runtime installs, caches, logs and large model-weight formats are excluded. '
                  + ('SECRETS EXCLUDED: credential files are not in this snapshot and secret-looking values were removed from the copies (see "secrets"). Conversation text and other content is copied as is.'
                     if exclude_secrets else 'Hermes .env and API keys are included: keep the backup folder private.')),
        **({'secrets': {'mode': 'excluded', 'files_not_copied': secret_files, 'values_removed': redacted, 'too_large_to_scan_not_copied': unscanned,
                        'reauthentication': 'Hermes provider logins (auth.json) and API keys (.env), Odysseus .env secrets, web login sessions, signing/app keys, email passwords, search API keys'}}
           if exclude_secrets else {}),
    })
    return destination, verify_backup(destination)


def create_backup(odysseus_root, hermes_home, backup_root, startup_files=(), task_xml=None, **options):
    return _build_backup(odysseus_root, hermes_home, backup_root, startup_files, task_xml, **options)[0]


def create_full_backup(environment=None, roots=None, exclude_secrets=False):
    """Snapshot everything this installation depends on. Returns (snapshot folder, verification report)."""
    env = dict(os.environ if environment is None else environment)
    roots = roots or default_roots(env)
    names = list(dict.fromkeys([env.get('ODYSSEUS_RESTART_TASK') or 'Odysseus AutoStart', 'Hermes_Gateway']))
    tasks = {name: xml for name in names if (xml := export_task(name))}
    startup = roots.get('startup')
    files = [path for path in sorted(startup.iterdir()) if path.is_file() and path.stat().st_size <= _MAX_STARTUP_FILE] if startup and startup.is_dir() else []
    return _build_backup(roots['odysseus'], roots.get('hermes'), roots['backup_dir'], files, task_xmls=tasks,
                         component_root=roots.get('component'), data_dir=roots.get('data'), environment=env, exclude_secrets=exclude_secrets)


# ── Verify / list ───────────────────────────────────────────────────────────────────────────
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


def snapshot_path(backup_root, name):
    """The folder for a snapshot name, or ValueError: only well-formed names inside the backup folder are accepted."""
    if not isinstance(name, str) or not _SNAPSHOT_NAME.match(name):
        raise ValueError('That is not a snapshot name.')
    path = safe_directory(backup_root) / name
    if not (path / 'manifest.json').is_file():
        raise ValueError('That snapshot does not exist.')
    return path


def list_backups(backup_root, limit=20):
    """Newest-first summaries read from manifests only (no hashing)."""
    base = Path(backup_root)
    if not base.is_dir():
        return []
    result = []
    for path in sorted(base.iterdir(), key=lambda p: p.name, reverse=True):
        if not _SNAPSHOT_NAME.match(path.name) or not (path / 'manifest.json').is_file():
            continue
        try:
            manifest = json.loads((path / 'manifest.json').read_text(encoding='utf-8'))
            entries = manifest['entries']
            result.append({'name': path.name, 'created_at': manifest.get('created_at'), 'files': len(entries),
                           'bytes': sum(item['bytes'] for item in entries), 'hermes': any(i['relative_path'].startswith('hermes/') for i in entries),
                           'tasks': sorted(manifest.get('tasks', {})), 'kind': path.name.split('-')[0],
                           'secrets_excluded': (manifest.get('secrets') or {}).get('mode') == 'excluded'})
        except (OSError, ValueError, KeyError, TypeError):
            result.append({'name': path.name, 'created_at': None, 'files': 0, 'bytes': 0, 'hermes': False, 'tasks': [], 'kind': 'unreadable'})
        if len(result) >= limit:
            break
    return result


# ── Restore ─────────────────────────────────────────────────────────────────────────────────
def _within(root, relative):
    """``root / relative`` provided it stays inside ``root`` and crosses no reparse point; else ValueError."""
    root = Path(root).absolute()
    candidate = Path(os.path.normpath(root / relative))
    if candidate == root or not candidate.is_relative_to(root):
        raise ValueError('A backup path points outside its destination; nothing was restored.')
    current = root
    for part in candidate.relative_to(root).parts[:-1]:
        current = current / part
        if current.exists() and _is_reparse(current):
            raise ValueError('A restore destination crosses a link or junction; nothing was restored.')
    if candidate.exists() and _is_reparse(candidate):
        raise ValueError('A restore destination is a link or junction; nothing was restored.')
    return candidate


def plan_restore(snapshot, odysseus_root, hermes_home=None, component_root=None, startup_dir=None, data_dir=None, overwrite_redacted=False):
    """Verify the snapshot, then decide where every file would go. Changes nothing."""
    snapshot = safe_directory(snapshot)
    verify_backup(snapshot)
    manifest = json.loads((snapshot / 'manifest.json').read_text(encoding='utf-8'))
    ody = safe_directory(odysseus_root)
    roots = {'odysseus': ody, 'hermes': safe_directory(hermes_home) if hermes_home else None,
             'component': safe_directory(component_root) if component_root else None,
             'startup': safe_directory(startup_dir) if startup_dir else None}
    data = safe_directory(data_dir) if data_dir else None
    items, skipped = [], []
    for item in manifest['entries']:
        relative = Path(item['relative_path'])
        area, rest = relative.parts[0], relative.parts[1:]
        if area == 'startup' and rest and (rest[0] == 'tasks' or rest == ('task.xml',)):
            continue
        root = roots.get(area)
        if area == 'odysseus' and rest[:1] == ('data',) and data is not None:
            root, rest = data, rest[1:]
        if root is None or not rest:
            skipped.append({'path': relative.as_posix(), 'reason': f'no destination was given for "{area}"'})
            continue
        target = _within(root, Path(*rest))
        status = 'new' if not target.exists() else ('same' if _digest(target) == item['sha256'] else 'overwrite')
        if status == 'overwrite' and item.get('redacted') and not overwrite_redacted:
            status = 'kept'           # the snapshot copy had its secrets removed: overwriting would erase the secrets in the live file
        items.append({'item': item, 'source': snapshot / relative, 'target': target, 'status': status, 'area': area})
    return {'snapshot': snapshot, 'manifest': manifest, 'items': items, 'skipped': skipped, 'roots': {'odysseus': ody, 'hermes': roots['hermes'], 'component': roots['component']}}


def _port_in_use(port):
    try:
        with socket.socket() as sock:
            if hasattr(socket, 'SO_EXCLUSIVEADDRUSE'):
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
            sock.bind(('127.0.0.1', int(port)))
        return False
    except OSError:
        return True


def _pid_running(pid):
    try:
        pid = int(pid)
        if os.name == 'nt':
            output = subprocess.run(['tasklist', '/FI', f'PID eq {pid}', '/NH'], capture_output=True, text=True, timeout=15,
                                    creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0)).stdout
            return str(pid) in output.split()
        os.kill(pid, 0)
        return True
    except (OSError, ValueError, subprocess.SubprocessError):
        return False


def running_services(hermes_home=None, ports=()):
    """Names of things that would still be using the files: busy ports and a live Hermes gateway."""
    busy = [f'port {port}' for port in ports if _port_in_use(port)]
    if hermes_home and (Path(hermes_home) / 'gateway.pid').is_file():
        try:
            record = json.loads((Path(hermes_home) / 'gateway.pid').read_text(encoding='utf-8'))
            pid = record.get('pid') if isinstance(record, dict) else record
        except (OSError, ValueError):
            pid = None
        if pid and _pid_running(pid):
            busy.append(f'the Hermes gateway (pid {pid})')
    return busy


def _restore_file(entry):
    source, target, item = entry['source'], entry['target'], entry['item']
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(f'{target.name}.restore-{uuid.uuid4().hex[:8]}')
    try:
        shutil.copy2(source, temporary)
        if _digest(temporary) != item['sha256']:
            raise ValueError('copy did not match the snapshot')
        if item['method'] == 'sqlite-online':          # a leftover journal from the old database must never meet the new one
            for suffix in ('-wal', '-shm', '-journal'):
                target.with_name(target.name + suffix).unlink(missing_ok=True)
        os.replace(temporary, target)
        if _digest(target) != item['sha256']:
            raise ValueError('restored file did not match the snapshot')
    finally:
        temporary.unlink(missing_ok=True)


def restore_backup(snapshot, odysseus_root, hermes_home=None, component_root=None, startup_dir=None, *, data_dir=None,
                   apply=False, safety_backup_root=None, busy=(), restore_tasks=False, task_importer=None, progress=None, overwrite_redacted=False):
    """Restore a snapshot. Dry run unless ``apply``. Overwrites files present in the snapshot; deletes nothing.

    ``safety_backup_root`` receives a snapshot of the current state before anything is overwritten (pass ``False`` to
    skip it explicitly). ``busy`` lists running things; any entry blocks an applied restore.
    """
    plan = plan_restore(snapshot, odysseus_root, hermes_home, component_root, startup_dir, data_dir, overwrite_redacted)
    manifest, items = plan['manifest'], plan['items']
    counts = {status: sum(1 for entry in items if entry['status'] == status) for status in ('new', 'same', 'overwrite', 'kept')}
    summary = {
        'snapshot': str(plan['snapshot']), 'created_at': manifest.get('created_at'), 'files': len(items),
        'bytes': sum(entry['item']['bytes'] for entry in items), **counts,
        'sqlite': sum(1 for entry in items if entry['item']['method'] == 'sqlite-online'),
        'overwrites': [str(entry['target']) for entry in items if entry['status'] == 'overwrite'][:25],
        'kept_live_secrets': [str(entry['target']) for entry in items if entry['status'] == 'kept'][:50],
        'secrets_excluded': (manifest.get('secrets') or {}).get('mode') == 'excluded',
        'skipped': plan['skipped'], 'tasks': sorted(manifest.get('tasks', {})), 'environment': manifest.get('environment', {}),
        'applied': False, 'restored': 0, 'failed': [], 'safety_backup': None, 'tasks_restored': [], 'tasks_failed': {},
    }
    if not apply:
        return summary
    if busy:
        raise RestoreBlocked('Stop these first: ' + ', '.join(busy))
    if safety_backup_root is None and counts['overwrite']:
        raise ValueError('A safety backup location is required before overwriting files.')
    if safety_backup_root:
        summary['safety_backup'] = str(create_backup(plan['roots']['odysseus'], plan['roots']['hermes'], safety_backup_root,
                                                     component_root=plan['roots']['component'], data_dir=data_dir, label='prerestore'))
    for index, entry in enumerate(items):
        if entry['status'] in ('same', 'kept'):
            continue
        try:
            _restore_file(entry)
            summary['restored'] += 1
        except (OSError, ValueError) as exc:
            summary['failed'].append({'path': str(entry['target']), 'error': str(exc)[:200]})
        if progress:
            progress(index + 1, len(items))
    if restore_tasks:
        importer = task_importer or import_task
        for name, relative in manifest.get('tasks', {}).items():
            try:
                importer(name, (plan['snapshot'] / relative).read_text(encoding='utf-8'))
                summary['tasks_restored'].append(name)
            except (OSError, ValueError) as exc:
                summary['tasks_failed'][name] = str(exc)[:300]
    summary['applied'] = True
    return summary

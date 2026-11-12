"""Odysseus workspace grants: live, fail-closed write roots for the managed Hermes gateway.

The agent decides where file tools may write in `agent.file_safety.get_safe_write_roots()`, which reads the process
environment on every call. An empty result means NO restriction, so "no workspace" cannot be expressed by an empty list.
This plugin rebinds that one function to return the roots granted in the Odysseus component's own configuration file
(`write_roots`, validated and written atomically by the component), re-read whenever the file changes, and to return a
folder nothing can live under when there are no grants or the file cannot be read. The environment is ignored on purpose.

What this is not: a sandbox. The terminal tool, MCP servers and approved commands run as the same user and are outside
this boundary; command approval is their only control. Revoking a grant stops the next file-tool write; it does not stop
a process that was already started.
"""
import json
import logging
import os
import threading
import time
from pathlib import Path

logger = logging.getLogger('odysseus.workspace_grants')
VERSION = '0.1.0'
CONFIG_ENV = 'ODYSSEUS_WORKSPACE_GRANTS'
STATUS_NAME = 'odysseus-workspace-grants.status.json'
SELFTEST_PATH = os.path.join(os.path.expanduser('~'), 'odysseus-workspace-grants-selftest.tmp')


class GrantsError(RuntimeError):
    pass


def _hermes_home():
    try:
        from hermes_constants import get_hermes_home
        return Path(get_hermes_home())
    except Exception:                                        # noqa: BLE001
        return Path(os.environ.get('HERMES_HOME') or Path.home() / '.hermes')


def _norm(path):
    return os.path.normcase(os.path.realpath(os.path.expanduser(os.fspath(path))))


def acceptable_root(path):
    """Cheap re-check of what the component already validated: never trust the file alone."""
    if not isinstance(path, str) or not os.path.isabs(path):
        return False
    real = Path(os.path.realpath(path))
    if real.parent == real:
        return False                                          # a drive root
    profile = Path(_norm(Path.home()))
    here = Path(_norm(real))
    return here != profile and here not in profile.parents


class Grants:
    def __init__(self, config_path, deny_root):
        self.config_path = Path(config_path) if config_path else None
        self.deny_root = os.fspath(deny_root)
        self._stamp = None
        self._roots = ()
        self._lock = threading.Lock()

    def roots(self):
        """The granted folders, or raises GrantsError. Re-read only when the file's size or mtime changed."""
        if self.config_path is None:
            raise GrantsError('no grants file is configured')
        try:
            info = self.config_path.stat()
        except OSError as exc:
            raise GrantsError(f'cannot read {self.config_path}: {exc}') from exc
        stamp = (info.st_mtime_ns, info.st_size)
        with self._lock:
            if stamp != self._stamp:
                try:
                    data = json.loads(self.config_path.read_text(encoding='utf-8'))
                except (OSError, ValueError) as exc:
                    raise GrantsError(f'{self.config_path} is unreadable or invalid: {exc}') from exc
                if not isinstance(data, dict):
                    raise GrantsError(f'{self.config_path} has an unexpected shape')
                raw = data.get('write_roots') or []
                if not isinstance(raw, list):
                    raise GrantsError('write_roots is not a list')
                kept = []
                for item in raw:
                    if acceptable_root(item):
                        kept.append(os.path.realpath(item))
                    else:
                        logger.error('Odysseus workspace grants: ignoring unacceptable grant %r', item)
                self._roots, self._stamp = tuple(kept), stamp
            return set(self._roots)


def _wrap(original, grants):
    def get_safe_write_roots():
        try:
            roots = grants.roots()
        except Exception:                                    # noqa: BLE001 - unknown means no workspace, never everything
            roots = set()
        return roots or {grants.deny_root}
    get_safe_write_roots._ody_wrapped = True
    get_safe_write_roots.__wrapped__ = original
    return get_safe_write_roots


def install(fs, grants):
    """Rebind `fs.get_safe_write_roots`. Returns (installed, reason). Nothing stays changed unless the self-test passes."""
    original = getattr(fs, 'get_safe_write_roots', None)
    if not callable(original):
        return False, 'agent.file_safety.get_safe_write_roots is missing'
    if getattr(original, '_ody_wrapped', False):
        return True, 'already installed'
    for name in ('is_write_denied', 'get_write_denied_error', '_classify_write_denial'):
        if not callable(getattr(fs, name, None)):
            return False, f'agent.file_safety.{name} is missing'
    fs.get_safe_write_roots = _wrap(original, grants)
    try:
        roots = fs.get_safe_write_roots()
        refused = fs.is_write_denied(SELFTEST_PATH) is True or any(os.path.normcase(SELFTEST_PATH).startswith(_norm(r) + os.sep) for r in roots)
        message = fs.get_write_denied_error(os.path.join(os.path.expanduser('~'), 'odysseus-workspace-grants-selftest-2.tmp'))
    except Exception as exc:                                 # noqa: BLE001
        fs.get_safe_write_roots = original
        return False, f'self-test raised {exc!r}'
    if not (isinstance(roots, set) and roots and (refused or message)):
        fs.get_safe_write_roots = original
        return False, 'the public predicates do not use the rebound function'
    return True, 'installed'


def write_status(home, installed, reason, grants):
    try:
        try:
            count = len(grants.roots())
        except GrantsError:
            count = None
        payload = {'plugin': 'odysseus-workspace-grants', 'version': VERSION, 'installed': bool(installed), 'reason': reason,
                   'grants_readable': count is not None, 'grants': count, 'checked_at': time.strftime('%Y-%m-%dT%H:%M:%S%z'), 'pid': os.getpid()}
        tmp = Path(home) / (STATUS_NAME + '.tmp')
        tmp.write_text(json.dumps(payload, indent=1), encoding='utf-8')
        os.replace(tmp, Path(home) / STATUS_NAME)
    except OSError:
        logger.exception('could not write the workspace grants status file')


def register(ctx):
    home = _hermes_home()
    grants = Grants(os.environ.get(CONFIG_ENV), home / 'vault' / 'odysseus-no-workspace')
    try:
        from agent import file_safety
        installed, reason = install(file_safety, grants)
    except Exception as exc:                                 # noqa: BLE001
        installed, reason = False, f'agent.file_safety could not be imported: {exc!r}'
    write_status(home, installed, reason, grants)
    if installed:
        logger.info('Odysseus workspace grants %s active (%s)', VERSION, reason)
    else:
        logger.error('Odysseus workspace grants NOT active: %s (the environment write roots, if any, still apply)', reason)

"""P0-2: enforce the never-allow list at the agent's single file-write classifier, in-process, with no WebUI edits.

`agent.file_safety.is_write_denied` and `get_write_denied_error` both call the module-level `_classify_write_denial`,
so rebinding that one name covers every importer (file tools, ACP clients, text-to-speech). The wrapper returns only
the classifier's own verdict values: None, 'credential', 'safe_root' or 'nt_namespace'. A never-allow hit returns
'credential', the hard deny that no prompt can override. If anything about the agent's shape is not as expected, nothing
is rebound and `install` reports why, so the caller can fail closed.
"""
import inspect
import json
import logging
import os
from pathlib import Path

logger = logging.getLogger('odysseus.ask_policy')

ALLOWED_VERDICTS = (None, 'credential', 'safe_root', 'nt_namespace')
SENTINEL = os.sep + 'odysseus-ask-policy-selftest' + os.sep + 'never-allow-sentinel'


class NeverAllowError(RuntimeError):
    pass


def _norm(path):
    return os.path.normcase(os.path.realpath(os.fspath(path)))


class NeverAllow:
    """Never-allow roots, read from the WebUI's saved list plus the optional policy file. Re-read when a file changes."""

    def __init__(self, hermes_home, extra_provider=lambda: ()):
        self.state_file = Path(hermes_home) / 'vault' / 'webui-trusted-workspaces.json'
        self._extra = extra_provider
        self._stamp = None
        self._cached = ()

    def entries(self):
        try:
            info = self.state_file.stat()
            stamp = (info.st_mtime_ns, info.st_size)
        except FileNotFoundError:
            stamp = None
        except OSError as exc:
            raise NeverAllowError(f'cannot read {self.state_file}: {exc}') from exc
        if stamp != self._stamp or self._stamp is None and stamp is not None:
            roots = ()
            if stamp is not None:
                try:
                    data = json.loads(self.state_file.read_text(encoding='utf-8'))
                except (OSError, ValueError) as exc:
                    raise NeverAllowError(f'{self.state_file} is unreadable or invalid: {exc}') from exc
                if not isinstance(data, dict) or data.get('version') != 1 or not isinstance(data.get('blocked', []), list):
                    raise NeverAllowError(f'{self.state_file} has an unexpected shape')
                roots = tuple(item['path'] for item in data.get('blocked', []) if isinstance(item, dict) and isinstance(item.get('path'), str))
            self._cached, self._stamp = roots, stamp
        return self._cached + tuple(self._extra())

    def health(self):
        try:
            return 'ok', len(self.entries())
        except NeverAllowError as exc:
            return f'UNREADABLE: {exc}', 0

    def covers(self, path, entry=False):
        roots = [_norm(root) for root in self.entries()]
        if not roots:
            return False
        candidates = {_norm(path)}
        if entry:
            candidates.add(_norm(os.path.join(os.path.realpath(os.path.dirname(os.fspath(path))), os.path.basename(os.fspath(path)))))
        return any(c == r or c.startswith(r.rstrip('\\/') + os.sep) for c in candidates for r in roots)


def _wrap(original, never):
    def _classify_write_denial(path, *args, **kwargs):
        if path == SENTINEL:
            return 'credential'
        entry = bool(kwargs.get('entry', args[0] if args else False))
        try:
            if never.covers(path, entry):
                return 'credential'
        except Exception:                                    # noqa: BLE001 - unknown means refuse, never allow
            return 'credential'
        return original(path, *args, **kwargs)
    _classify_write_denial._ody_wrapped = True
    _classify_write_denial.__wrapped__ = original
    return _classify_write_denial


def install(fs, never):
    """Rebind `fs._classify_write_denial`. Returns (installed, reason). Nothing is changed unless every check passes."""
    original = getattr(fs, '_classify_write_denial', None)
    if not callable(original):
        return False, 'agent.file_safety._classify_write_denial is missing'
    if getattr(original, '_ody_wrapped', False):
        return True, 'already installed'
    try:
        parameters = inspect.signature(original).parameters
    except (TypeError, ValueError) as exc:
        return False, f'cannot inspect _classify_write_denial ({exc})'
    if 'path' not in parameters or 'entry' not in parameters:
        return False, f'_classify_write_denial changed shape: {tuple(parameters)}'
    for name in ('is_write_denied', 'get_write_denied_error'):
        if not callable(getattr(fs, name, None)):
            return False, f'agent.file_safety.{name} is missing'
    fs._classify_write_denial = _wrap(original, never)
    try:
        refused = fs.is_write_denied(SENTINEL) is True
        message = fs.get_write_denied_error(SENTINEL)
        verdict = fs._classify_write_denial(os.path.join(os.path.expanduser('~'), 'odysseus-selftest.tmp'))
    except Exception as exc:                                 # noqa: BLE001
        fs._classify_write_denial = original
        return False, f'self-test raised {exc!r}'
    if not (refused and isinstance(message, str) and message and verdict in ALLOWED_VERDICTS):
        fs._classify_write_denial = original
        return False, 'the public predicates do not use the rebound classifier'
    return True, 'installed'

"""Odysseus ask-policy: a user-level Hermes plugin. See README.md for what it does and does not do.

Fail-closed rules implemented here: any error while deciding returns a block; the host turns a timed-out callback into a
block as well. What no plugin can do is protect against itself being absent or disabled: the status file and the
Odysseus indicator exist so that case is visible.
"""
import json
import logging
import os
from pathlib import Path
import sys
import threading
import time

from . import filesafety, policy

logger = logging.getLogger('odysseus.ask_policy')
VERSION = '0.1.0'
STATUS_NAME = 'ask-policy.status.json'
_STATUS_REFRESH_SECONDS = 30


def _hermes_home():
    try:
        from hermes_constants import get_hermes_home
        return Path(get_hermes_home())
    except Exception:                                        # noqa: BLE001
        return Path(os.environ.get('HERMES_HOME') or Path.home() / '.hermes')


def _redactor():
    try:
        from agent.redact import redact_sensitive_text
        return redact_sensitive_text
    except Exception:                                        # noqa: BLE001
        return None


class AskPolicy:
    def __init__(self, home):
        self.home = Path(home)
        self._policy = policy.Policy()
        self._policy_stamp = 'unset'
        self.never = filesafety.NeverAllow(self.home, extra_provider=lambda: self.current_policy().never_allow)
        self.file_safety_ok = False
        self.file_safety_reason = 'not installed'
        self._status_written = 0.0
        self._lock = threading.Lock()

    # -- policy file (re-read when it changes) ------------------------------------------------------------------
    def current_policy(self):
        path = policy.policy_path(self.home)
        try:
            info = path.stat()
            stamp = (info.st_mtime_ns, info.st_size)
        except OSError:
            stamp = None
        if stamp != self._policy_stamp:
            self._policy = policy.load_policy(self.home)
            self._policy_stamp = stamp
            for problem in self._policy.problems:
                logger.error('Odysseus ask-policy: %s', problem)
        return self._policy

    # -- file-write protection (P0-2) ---------------------------------------------------------------------------
    def install_file_safety(self):
        try:
            from agent import file_safety
            self.file_safety_ok, self.file_safety_reason = filesafety.install(file_safety, self.never)
        except Exception as exc:                             # noqa: BLE001
            self.file_safety_ok, self.file_safety_reason = False, f'could not import agent.file_safety ({exc})'
        if not self.file_safety_ok:
            banner = f'ODYSSEUS ASK-POLICY: FILE-WRITE PROTECTION UNAVAILABLE ({self.file_safety_reason}). File writes are blocked until the plugin is updated.'
            logger.error(banner)
            print(banner, file=sys.stderr, flush=True)
        self.write_status()

    # -- visible state -------------------------------------------------------------------------------------------
    def write_status(self):
        try:
            current = self.current_policy()
            health, entry_count = self.never.health()
            data = {
                'plugin': 'odysseus-ask-policy', 'version': VERSION, 'pid': os.getpid(), 'updated_at': time.time(),
                'hook': 'pre_tool_call', 'file_safety': 'installed' if self.file_safety_ok else f'UNAVAILABLE: {self.file_safety_reason}',
                'never_allow': health, 'never_allow_entries': entry_count, 'yolo_policy': current.yolo_policy,
                'gate_file_tools': current.gate_file_tools, 'policy_problems': list(current.problems),
            }
            target = self.home / 'odysseus' / STATUS_NAME
            target.parent.mkdir(parents=True, exist_ok=True)
            temporary = target.with_name(target.name + f'.{os.getpid()}.tmp')
            temporary.write_text(json.dumps(data, indent=2), encoding='utf-8')
            os.replace(temporary, target)
            self._status_written = time.monotonic()
        except Exception:                                    # noqa: BLE001 - the status file is informational
            logger.debug('could not write the ask-policy status file', exc_info=True)

    def _refresh_status(self):
        if time.monotonic() - self._status_written > _STATUS_REFRESH_SECONDS and self._lock.acquire(blocking=False):
            try:
                self.write_status()
            finally:
                self._lock.release()

    # -- the hook -----------------------------------------------------------------------------------------------
    @staticmethod
    def _bypasses():
        """(session_yolo, persistent_bypass): what would make the human approval gate skip the question."""
        persistent = session = False
        try:
            from tools import approval, approval_context
            persistent = bool(approval._YOLO_MODE_FROZEN) or approval_context._get_approval_mode() == 'off'
            session = bool(approval._yolo_active())
        except Exception:                                    # noqa: BLE001 - cannot tell, so assume the worst
            persistent = True
        return session, persistent

    def pre_tool_call(self, tool_name=None, args=None, session_id='', **_ignored):
        try:
            self._refresh_status()
            current = self.current_policy()
            decision = policy.decide(tool_name, args, current, self.file_safety_ok, _redactor())
            if decision.action == 'allow':
                return None
            if decision.action == 'block':
                return {'action': 'block', 'message': decision.message}
            session_yolo, persistent = self._bypasses()
            if not session_yolo and session_id:
                try:
                    from tools import approval
                    session_yolo = bool(approval.is_session_yolo_enabled(session_id))
                except Exception:                            # noqa: BLE001
                    session_yolo = False
            if persistent:
                return {'action': 'block', 'message': f'{policy.LABEL} blocked {tool_name}: approvals.mode is "off" or launch YOLO is on, which would skip the human question. Set approvals.mode to manual.'}
            if session_yolo and current.yolo_policy == 'block':
                return {'action': 'block', 'message': f'{policy.LABEL} blocked {tool_name}: YOLO is on for this session and this policy does not respect YOLO. Turn YOLO off to be asked instead.'}
            return {'action': 'approve', 'message': decision.message, 'rule_key': decision.rule_key}
        except Exception:                                    # noqa: BLE001
            logger.exception('Odysseus ask-policy failed; blocking %r (fail-closed)', tool_name)
            return {'action': 'block', 'message': f'{policy.LABEL} blocked {tool_name}: the policy itself failed (fail-closed).'}


_INSTANCE = None


def register(ctx):
    global _INSTANCE
    _INSTANCE = AskPolicy(_hermes_home())
    _INSTANCE.install_file_safety()
    ctx.register_hook('pre_tool_call', _INSTANCE.pre_tool_call)
    logger.info('Odysseus ask-policy %s active (file safety: %s)', VERSION, _INSTANCE.file_safety_reason)

"""Grant / revoke a write root for the managed Hermes gateway, from the Projects dashboard.

This adds no permission mechanism. It is a confirmation, safety-check and audit layer in front of the component's own
``set_write_roots`` / ``inspect_root`` / ``root_problem`` (the list the gateway's workspace-grants plugin re-reads on every file call).

Rules enforced here:
  * two steps: ``preview`` (read-only) issues a short-lived, single-use confirmation bound to the action, the canonical path, the
    signed-in user and the exact current list of roots; ``apply`` refuses anything else, so a stale or replayed request changes nothing
  * a grant is exactly one folder the component accepts (top folder of one Git repository or worktree); never a parent, a drive root,
    the user profile, credentials, Odysseus or Hermes; never ``clear``; a revoke removes only the exact existing entry
  * a grant must be a folder the dashboard knows as part of the project it names (its registered folders or Git worktrees)
  * granting also requires typing the folder's name; revoking (which only narrows access) needs one confirmation
  * every apply, success or failure, is appended to an audit file the gateway cannot write (outside every write root)
"""
import hashlib
import json
import os
import secrets
import threading
import time
from pathlib import Path

CONFIRMATION_SECONDS = 120
MAX_PENDING = 16
NOTE = ('This changes which folders the Hermes file tools may write. It is an application rule, not OS containment: a terminal, MCP servers, '
        'git hooks and any program running as you are not limited by it.')


class GrantError(Exception):
    def __init__(self, message, status=409):
        super().__init__(message)
        self.status = status


def _norm(path):
    return os.path.normcase(os.path.normpath(str(path)))


def _digest(roots):
    return hashlib.sha256('\n'.join(sorted(_norm(r) for r in roots)).encode('utf-8')).hexdigest()


class GrantManager:
    def __init__(self, dashboard, get_component, audit_path, clock=time.time):
        self.dashboard = dashboard
        self.get_component = get_component
        self.audit_path = Path(audit_path)
        self.clock = clock
        self._pending = {}
        self._lock = threading.Lock()

    # -- step 1: read-only description + confirmation ----------------------------------------------------------------------------
    def preview(self, action, path, project_id, user):
        if action not in ('grant', 'revoke'):
            raise GrantError('action must be grant or revoke', 422)
        if not isinstance(path, str) or not os.path.isabs(path) or len(path) > 600 or '\x00' in path:
            raise GrantError('an absolute folder path is required', 422)
        component = self.get_component()
        try:
            roots = component.write_roots()
            config = component._require_managed()
        except Exception as exc:
            raise GrantError('The Webapp gateway is not a Hermes component managed by Odysseus here; grants cannot be changed from this page (%s).' % type(exc).__name__)
        canonical = os.path.normpath(os.path.realpath(path))
        existing = next((r for r in roots if _norm(r) == _norm(canonical) or _norm(os.path.realpath(r)) == _norm(canonical)), None)
        report = {'action': action, 'path': path, 'canonical': canonical, 'roots_now': roots, 'warnings': [], 'problems': [], 'worktree': None, 'covers_existing': [],
                  'note': NOTE, 'requires_typed_name': action == 'grant', 'typed_name': os.path.basename(canonical.rstrip('\\/')) if action == 'grant' else None}
        if action == 'revoke':
            if not existing:
                report['problems'].append('This exact folder is not a granted write root; nothing to revoke. Only exact grants can be removed here.')
            else:
                canonical = existing
                report['canonical'] = existing
        else:
            if existing:
                report['problems'].append('This folder is already a granted write root.')
            known = self._known_paths(project_id)
            if known is None:
                raise GrantError('Unknown project', 404)
            match = next((k for k in known if _norm(os.path.realpath(k['path'])) == _norm(canonical)), None)
            if not match:
                report['problems'].append('This folder is not one of the registered folders or Git worktrees of the selected project.')
            else:
                report['worktree'] = match
            problem = component.root_problem(canonical, config)
            if problem:
                report['problems'].append('The component refuses it: %s.' % problem)
            try:
                inspected = component.inspect_root(canonical)
            except Exception:
                inspected = {}
            report['warnings'] += list(inspected.get('notes') or [])
            if inspected.get('kind') == 'linked worktree':
                common = inspected.get('common_git_dir')
                common_root = str(Path(common).parent) if common else None
                if common and not any(_norm(common).startswith(_norm(r) + os.sep) for r in roots + [canonical]):
                    report['warnings'].append('Git data for this worktree is in %s, which is outside every authorized folder. Commits and branch changes made from here write there, and the guards that check the working folder will not cover it.' % common)
                shared = [w['path'] for w in (known or []) if w['path'] and _norm(w['path']) != _norm(canonical)]
                if shared:
                    report['warnings'].append('%d other worktree(s) of this repository share that Git data: a commit or branch change here is visible in all of them.' % len(shared))
                if common_root and _norm(common_root) == _norm(canonical):
                    pass
            report['covers_existing'] = [r for r in roots if _norm(r).startswith(_norm(canonical) + os.sep)]
            if report['covers_existing']:
                report['warnings'].append('This folder contains %d folder(s) that are already granted; the grant list itself is not changed for them.' % len(report['covers_existing']))
        if not report['problems']:
            token = secrets.token_urlsafe(24)
            with self._lock:
                now = self.clock()
                self._pending = {t: p for t, p in self._pending.items() if p['expires'] > now}
                while len(self._pending) >= MAX_PENDING:
                    self._pending.pop(next(iter(self._pending)))
                self._pending[token] = {'action': action, 'canonical': canonical, 'user': user, 'expires': now + CONFIRMATION_SECONDS, 'roots': _digest(roots),
                                        'warnings': list(report['warnings']), 'project_id': project_id}
            report['confirmation'] = token
            report['expires_in'] = CONFIRMATION_SECONDS
        return report

    def _known_paths(self, project_id):
        detail = self.dashboard.detail(project_id) if project_id else None
        if detail is None:
            return None
        paths = [{'path': w['path'], 'kind': 'worktree', 'branch': w.get('branch')} for w in detail['worktrees'] if not w.get('prunable')]
        listed = {_norm(w['path']) for w in paths}
        return paths + [{'path': p, 'kind': 'project folder'} for p in detail['paths'] if _norm(p) not in listed]

    # -- step 2: confirmed change --------------------------------------------------------------------------------------------------
    def apply(self, token, typed_name, user, remote=None):
        component = self.get_component()
        with self._lock:
            pending = self._pending.pop(token, None) if isinstance(token, str) else None
        if not pending or pending['expires'] <= self.clock():
            raise GrantError('This confirmation expired or was already used. Review the change again.')
        if pending['user'] != user:
            raise GrantError('This confirmation belongs to a different signed-in user.', 403)
        action, canonical = pending['action'], pending['canonical']
        if action == 'grant' and typed_name != os.path.basename(canonical.rstrip('\\/')):
            self._audit(user, action, canonical, [], [], pending['warnings'], 'refused: folder name not confirmed', remote)
            raise GrantError('Type the folder name exactly to confirm a grant.', 422)
        before = component.write_roots()
        if _digest(before) != pending['roots']:
            self._audit(user, action, canonical, before, before, pending['warnings'], 'refused: grants changed since the preview', remote)
            raise GrantError('The list of granted folders changed since you reviewed it. Review the change again.')
        try:
            if action == 'grant':
                after = component.set_write_roots(add=[canonical])
            else:
                after = component.set_write_roots(remove=[canonical])
        except Exception as exc:
            self._audit(user, action, canonical, before, before, pending['warnings'], 'failed: %s' % str(exc)[:200], remote)
            raise GrantError('The component refused the change: %s' % str(exc)[:300])
        after = component.write_roots()
        expected = {_norm(r) for r in before} | ({_norm(canonical)} if action == 'grant' else set())
        if action == 'revoke':
            expected = {_norm(r) for r in before} - {_norm(canonical)}
        if {_norm(r) for r in after} != expected:
            self._audit(user, action, canonical, before, after, pending['warnings'], 'unexpected result', remote)
            raise GrantError('The grant list is not what was expected after the change; it was left as written and recorded in the audit log.')
        self._audit(user, action, canonical, before, after, pending['warnings'], 'applied', remote)
        return {'ok': True, 'action': action, 'canonical': canonical, 'roots': after, 'live': 'The gateway re-reads the list on its next file operation; no restart is needed.'}

    # -- audit ---------------------------------------------------------------------------------------------------------------------
    def _audit(self, user, action, path, before, after, warnings, result, remote):
        record = {'ts': self.clock(), 'user': user, 'action': action, 'path': path, 'before': list(before), 'after': list(after), 'warnings': warnings,
                  'result': result, 'source': 'projects-dashboard', 'remote': remote}
        self.audit_path.parent.mkdir(parents=True, exist_ok=True)
        with self._lock, open(self.audit_path, 'a', encoding='utf-8', newline='\n') as handle:
            handle.write(json.dumps(record, ensure_ascii=False) + '\n')
            handle.flush()
            os.fsync(handle.fileno())

    def audit(self, limit=20):
        try:
            lines = self.audit_path.read_text(encoding='utf-8').splitlines()
        except OSError:
            return []
        out = []
        for line in lines[-max(1, min(int(limit), 200)):]:
            try:
                out.append(json.loads(line))
            except ValueError:
                continue
        return list(reversed(out))


class Runtime:
    """One dashboard and one grant manager per process (the manager holds the pending confirmations)."""

    def __init__(self, environment=None):
        from src import projects_dashboard
        env = os.environ if environment is None else environment
        self.environment = env
        self.dashboard = projects_dashboard.from_environment(env)
        self.root = self.dashboard.component_root
        self._component = None
        self.grants = GrantManager(self.dashboard, self._get_component, Path(self.root) / 'managed' / 'webapp' / 'grant-audit.jsonl')

    def _get_component(self):
        if self._component is None:
            from src.hermes_webapp_component import WebappComponent
            from src.runtime_paths import get_app_root
            self._component = WebappComponent(self.root, application_root=get_app_root(), environment=self.environment)
        return self._component

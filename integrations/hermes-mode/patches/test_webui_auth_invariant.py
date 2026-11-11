"""Invariant: the browser origin guard fails OPEN for header-less clients on purpose (curl, MCP and CLI send no
Origin/Referer/Sec-Fetch-Site), so the password gate is the only thing between a local process and the
state-changing routes. With a password set, every such route must refuse a request that carries no credentials,
whatever headers it sends. Do not 'fix' the fail-open by breaking CLI/MCP: keep auth on.

Run from OUTSIDE the pinned tree (nothing is copied into it):
    HERMES_WEBUI_ROOT=F:/hermes-webui <webui python> test_webui_auth_invariant.py"""
import ast
import os
from pathlib import Path
import re
import sys
import unittest
from unittest import mock

ROOT = Path(os.environ.get('HERMES_WEBUI_ROOT') or Path(__file__).resolve().parents[1])
sys.path.insert(0, str(ROOT))
for extra in filter(None, os.environ.get('HERMES_TEST_SITE_PACKAGES', '').split(os.pathsep)):
    sys.path.append(extra)

from api import auth, routes  # noqa: E402

WRITE_HANDLERS = ('handle_post', 'handle_put', 'handle_patch', 'handle_delete')
# The ONLY paths reachable without credentials. Hard-coded on purpose: adding to auth.PUBLIC_PATHS must fail a test
# and be reviewed. /health, /api/auth/status and /login are the liveness and login surface; the rest are login-flow
# necessities (OIDC/passkey), PWA manifests and the shared-link landing page.
EXPLICIT_PUBLIC_PATHS = frozenset({
    '/health', '/api/auth/status', '/login', '/favicon.ico', '/sw.js',
    '/api/auth/login', '/api/auth/oidc/start', '/api/auth/oidc/callback',
    '/api/auth/passkey/options', '/api/auth/passkey/login',
    '/share', '/manifest.json', '/manifest.webmanifest', '/session/manifest.json', '/session/manifest.webmanifest',
})
EXPLICIT_PUBLIC_PREFIXES = ('/static/', '/session/static/', '/share/', '/api/share/')
EXPLICIT_PUBLIC_EXCEPT = frozenset({'/api/share/create', '/api/share/revoke'})
HEADER_SHAPES = (
    {},
    {'Origin': 'http://127.0.0.1:8787'},
    {'Origin': 'http://evil.test:8787', 'Host': 'evil.test:8787', 'Sec-Fetch-Site': 'same-origin'},
    {'Sec-Fetch-Site': 'none'},
    {'Origin': 'null'},
)


class FakeWire:
    def write(self, chunk):
        pass


class FakeHandler:
    def __init__(self, headers=None):
        self.headers = headers or {}
        self.status = None
        self.wfile = FakeWire()

    def send_response(self, status, *args):
        self.status = status

    def send_header(self, *args):
        pass

    def end_headers(self):
        pass

    def log_message(self, *args):
        pass


class Parsed:
    def __init__(self, path):
        self.path = path
        self.query = ''


def state_changing_paths():
    tree = ast.parse((ROOT / 'api/routes.py').read_text(encoding='utf-8'))
    found = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name in WRITE_HANDLERS:
            for child in ast.walk(node):
                if isinstance(child, ast.Constant) and isinstance(child.value, str) and child.value.startswith('/api/') and ' ' not in child.value:
                    found.add(child.value.rstrip('/'))
    return sorted(found)


_URL_PATH = re.compile(r'^/[A-Za-z0-9_\-./]*$')


def all_route_paths():
    """Every URL-path literal inside any request handler of api/routes.py, GET included (the original leak was a GET)."""
    tree = ast.parse((ROOT / 'api/routes.py').read_text(encoding='utf-8'))
    found = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and (node.name.startswith(('handle_', '_handle_'))):
            for child in ast.walk(node):
                if isinstance(child, ast.Constant) and isinstance(child.value, str) and len(child.value) > 1 and _URL_PATH.match(child.value):
                    found.add(child.value.rstrip('/') or '/')
    found.update(('/', '/api/sessions', '/api/session', '/api/models', '/api/providers', '/api/memory', '/api/skills'))
    return sorted(found)


def is_explicitly_public(path):
    return path in EXPLICIT_PUBLIC_PATHS or (path.startswith(EXPLICIT_PUBLIC_PREFIXES) and path not in EXPLICIT_PUBLIC_EXCEPT)


class PasswordOn(unittest.TestCase):
    def setUp(self):
        env = mock.patch.dict(os.environ, {'HERMES_WEBUI_PASSWORD': 'invariant-test-only'})
        env.start()
        self.addCleanup(env.stop)
        for name in ('HERMES_WEBUI_TRUSTED_AUTH_HEADER', 'HERMES_WEBUI_TRUSTED_GROUPS_HEADER'):
            os.environ.pop(name, None)
        for patcher in (mock.patch.object(auth, '_AUTH_HASH_COMPUTED', False), mock.patch.object(auth, '_AUTH_HASH_CACHE', None)):
            patcher.start()
            self.addCleanup(patcher.stop)
        self.assertTrue(auth.is_auth_enabled())

    def test_the_route_list_is_not_empty_and_covers_the_dangerous_endpoints(self):
        paths = state_changing_paths()
        self.assertGreater(len(paths), 60)
        for needed in ('/api/chat/start', '/api/terminal/start', '/api/commands/exec', '/api/approval/respond', '/api/shutdown',
                       '/api/trusted-workspaces/approve', '/api/trusted-workspaces/decide', '/api/session/delete'):
            self.assertIn(needed, paths)

    def test_every_state_changing_route_refuses_a_credential_less_request(self):
        public = set(auth.PUBLIC_PATHS) | {'/api/share/create', '/api/share/revoke'}
        for extra in HEADER_SHAPES:
            reached = []
            for path in state_changing_paths():
                handler = FakeHandler(dict(extra))
                allowed = auth.check_auth(handler, Parsed(path))
                if allowed and path not in public and not path.startswith(('/api/share/', '/static/')):
                    reached.append(path)
                elif not allowed:
                    self.assertIn(handler.status, (401, 403, 302), (path, handler.status))
            self.assertEqual(reached, [], f'reachable without credentials with headers {extra}')

    def test_the_set_of_public_paths_is_exactly_the_reviewed_one(self):
        self.assertEqual(set(auth.PUBLIC_PATHS), set(EXPLICIT_PUBLIC_PATHS), 'auth.PUBLIC_PATHS changed: review it, then update the test')

    def test_every_get_and_other_route_refuses_unless_explicitly_public(self):
        paths = all_route_paths()
        self.assertGreater(len(paths), 200)
        for needed in ('/api/sessions', '/api/terminal/output', '/api/approval/pending', '/api/session/yolo', '/api/trusted-workspaces',
                       '/health', '/api/auth/status', '/login'):
            self.assertIn(needed, paths)
        for extra in HEADER_SHAPES:
            leaked = []
            for path in paths:
                handler = FakeHandler(dict(extra))
                allowed = auth.check_auth(handler, Parsed(path))
                if allowed and not is_explicitly_public(path):
                    leaked.append(path)
                elif not allowed:
                    self.assertIn(handler.status, (401, 403, 302), (path, handler.status))
            self.assertEqual(leaked, [], f'reachable without credentials with headers {extra}')

    def test_the_origin_guard_fails_open_for_headerless_clients_so_auth_must_stay_on(self):
        handler = FakeHandler({'Host': '127.0.0.1:8787'})
        self.assertIs(routes._check_same_origin_browser_request(handler), True)       # documented fail-open (CLI/MCP/curl)
        self.assertIs(auth.check_auth(FakeHandler({'Host': '127.0.0.1:8787'}), Parsed('/api/chat/start')), False)


class PasswordOff(unittest.TestCase):
    def test_without_a_password_the_same_request_is_not_refused_which_is_why_the_password_matters(self):
        with mock.patch.object(auth, 'is_auth_enabled', lambda: False):
            self.assertIs(auth.check_auth(FakeHandler(), Parsed('/api/terminal/start')), True)


if __name__ == '__main__':
    unittest.main()

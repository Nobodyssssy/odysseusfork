"""Invariant: the browser origin guard fails OPEN for header-less clients on purpose (curl, MCP and CLI send no
Origin/Referer/Sec-Fetch-Site), so the password gate is the only thing between a local process and the
state-changing routes. With a password set, every such route must refuse a request that carries no credentials,
whatever headers it sends. Do not 'fix' the fail-open by breaking CLI/MCP: keep auth on.

Run with the WebUI's own interpreter:  python -m unittest tests.test_webui_auth_invariant"""
import ast
import os
from pathlib import Path
import sys
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from api import auth, routes  # noqa: E402

WRITE_HANDLERS = ('handle_post', 'handle_put', 'handle_patch', 'handle_delete')
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

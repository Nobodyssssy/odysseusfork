"""Official Hermes Gateway backend: status, one-use launch link translation, launch route, service controls."""
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import socket
import sys
import threading
import unittest
from unittest.mock import Mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

import src.application_modes as modes
from src import service_controls as controls

LINK = 'http://127.0.0.1:{port}/?native=0#launch=SECRETSECRETSECRET'
SECRET = 'SECRETSECRETSECRET'


def free_port():
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        return sock.getsockname()[1]


@contextmanager
def server(routes):
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            status, body = routes.get(self.path, (404, b'{}'))
            self.send_response(status)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    httpd = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        yield httpd.server_address[1]
    finally:
        httpd.shutdown()
        httpd.server_close()


HEALTH = json.dumps({'ok': True, 'version': '1', 'displayVersion': '1', 'commit': None, 'auth_required': False}).encode()
GATED = json.dumps({'ok': True, 'version': '1', 'displayVersion': '1', 'commit': None, 'auth_required': True}).encode()
WEBUI = json.dumps({'status': 'ok', 'sessions': 0, 'server_started_at': 1.0, 'active_runs': 0}).encode()


def gateway_service(port, launcher=(), env=None):
    config = modes.ModeConfig(url=f'http://127.0.0.1:{port}/', backend='gateway', launcher=tuple(launcher), environment=env or {})
    return modes.ModeService(config)


def helper(body):
    """A launcher command that prints *body* and exits 0 (a stand-in for scripts/hermes-webapp-launch.py)."""
    return (sys.executable, '-c', 'import sys; sys.stdout.write(%r)' % body)


class UrlTranslationTests(unittest.TestCase):
    def test_gateway_link_becomes_the_browser_address_with_query_and_fragment_kept(self):
        url = modes.browser_launch_url(LINK.format(port=19120), 'http://127.0.0.1:19120/', 7000)
        self.assertEqual(url, 'http://hermes.localhost:7000/?native=0#launch=' + SECRET)

    def test_link_already_on_the_browser_address_is_accepted(self):
        url = modes.browser_launch_url('http://hermes.localhost:7000/#t=1', 'http://127.0.0.1:19120/', 7000)
        self.assertEqual(url, 'http://hermes.localhost:7000/#t=1')

    def test_any_other_destination_is_refused_without_echoing_the_link(self):
        for raw in ('http://evil.test:19120/#launch=' + SECRET, 'http://127.0.0.1:19121/#launch=' + SECRET, 'https://127.0.0.1:19120/#launch=' + SECRET,
                    'http://user:pw@127.0.0.1:19120/#launch=' + SECRET, 'http://hermes.localhost:7001/#launch=' + SECRET,
                    'http://127.0.0.1.evil.test:19120/#launch=' + SECRET, 'http://localhost:19120/#launch=' + SECRET,
                    'javascript:alert(1)', '//evil.test/#' + SECRET, 'http://127.0.0.1:19120/ #' + SECRET, 'http://127.0.0.1:19120/\n#' + SECRET,
                    '', None, 42, 'http://127.0.0.1:19120/' + 'a' * 5000):
            with self.subTest(raw=str(raw)[:50]), self.assertRaises(RuntimeError) as caught:
                modes.browser_launch_url(raw, 'http://127.0.0.1:19120/', 7000)
            self.assertNotIn(SECRET, str(caught.exception))

    def test_unknown_browser_port_is_refused(self):
        with self.assertRaises(RuntimeError):
            modes.browser_launch_url(LINK.format(port=19120), 'http://127.0.0.1:19120/', None)


class ConfigTests(unittest.TestCase):
    def environment(self, tmp):
        python = Path(tmp) / 'python.exe'
        python.write_text('')
        (Path(tmp) / 'home').mkdir(exist_ok=True)
        (Path(tmp) / 'agent').mkdir(exist_ok=True)
        return {'ODYSSEUS_HERMES_BACKEND': 'gateway', 'ODYSSEUS_HERMES_MODE_URL': 'http://127.0.0.1:19120/',
                'ODYSSEUS_HERMES_GATEWAY_HOME': str(Path(tmp) / 'home'), 'ODYSSEUS_HERMES_GATEWAY_PYTHON': str(python),
                'ODYSSEUS_HERMES_GATEWAY_AGENT_DIR': str(Path(tmp) / 'agent'), 'PYTHONPATH': 'x', 'ODYSSEUS_SECRET_THING': 'y', 'PATH': 'p'}

    def test_gateway_backend_is_read_and_the_helper_gets_a_clean_environment(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            config = modes.ModeConfig.from_environment(self.environment(tmp))
        self.assertEqual(config.backend, 'gateway')
        self.assertEqual(config.url, 'http://127.0.0.1:19120/')
        self.assertTrue(config.launcher[-3].endswith('hermes-webapp-launch.py'))
        self.assertEqual(config.environment['HERMES_HOME'], str(Path(tmp) / 'home'))
        self.assertEqual(config.environment['PYTHONPATH'], str(Path(tmp) / 'agent'))   # hermes_cli comes from the configured checkout only
        self.assertFalse([key for key in config.environment if key.startswith('ODYSSEUS_')])
        self.assertEqual(config.environment['PATH'], 'p')

    def test_incomplete_or_wrong_configuration_fails_closed(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            for drop in ('ODYSSEUS_HERMES_GATEWAY_HOME', 'ODYSSEUS_HERMES_GATEWAY_PYTHON', 'ODYSSEUS_HERMES_GATEWAY_AGENT_DIR', 'ODYSSEUS_HERMES_MODE_URL'):
                env = self.environment(tmp)
                del env[drop]
                with self.subTest(drop=drop), self.assertRaises(ValueError):
                    modes.ModeConfig.from_environment(env)
                self.assertIsNotNone(modes.create_mode_service(env).error)
            env = self.environment(tmp)
            env['ODYSSEUS_HERMES_GATEWAY_PYTHON'] = str(Path(tmp) / 'missing.exe')
            self.assertIsNotNone(modes.create_mode_service(env).error)
            env = self.environment(tmp)
            env['ODYSSEUS_HERMES_MODE_URL'] = 'http://example.test:19120/'
            self.assertIsNotNone(modes.create_mode_service(env).error)

    def test_disabled_flag_still_wins(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            env = {**self.environment(tmp), 'ODYSSEUS_HERMES_DISABLED': '1'}
            self.assertIsNone(modes.ModeConfig.from_environment(env))


class StatusTests(unittest.TestCase):
    def test_official_webapp_is_recognised_and_reports_its_sign_in_gate(self):
        with server({'/api/health': (200, HEALTH)}) as port:
            status = gateway_service(port).status()
        self.assertEqual((status['state'], status['backend'], status['sign_in_gate']), ('ready', 'gateway', False))
        with server({'/api/health': (200, GATED)}) as port:
            self.assertTrue(gateway_service(port).status()['sign_in_gate'])

    def test_the_old_webui_or_any_other_listener_is_not_the_gateway(self):
        for routes in ({'/health': (200, WEBUI), '/api/health': (404, b'{}')}, {'/api/health': (200, b'{"ok":true}')},
                       {'/api/health': (200, b'not json')}, {'/api/health': (200, b'{"ok":false,"auth_required":false,"displayVersion":"1"}')}):
            with self.subTest(routes=list(routes)), server(routes) as port:
                self.assertEqual(gateway_service(port).status()['state'], 'conflict')

    def test_absent_gateway_is_reported_without_raising(self):
        self.assertEqual(gateway_service(free_port()).status()['state'], 'stopped')

    def test_the_gateway_is_never_started_stopped_or_restarted_from_here(self):
        service = gateway_service(free_port())
        with self.assertRaises(RuntimeError):
            service.start()
        for action in (service.stop, service.restart):
            with self.assertRaises(controls.Unavailable):
                action(force=True)


class LaunchTests(unittest.TestCase):
    def launch(self, launcher, health=HEALTH, port=7000):
        with server({'/api/health': (200, health)}) as gw:
            return gateway_service(gw, launcher).launch(port), gw

    def test_helper_link_is_returned_translated(self):
        with server({'/api/health': (200, HEALTH)}) as gw:
            body = json.dumps({'ok': True, 'url': LINK.format(port=gw)}) + '\n'
            url = gateway_service(gw, helper(body)).launch(7000)
        self.assertEqual(url, 'http://hermes.localhost:7000/?native=0#launch=' + SECRET)

    def test_failures_never_carry_the_link(self):
        with server({'/api/health': (200, HEALTH)}) as gw:
            link = LINK.format(port=gw)
            cases = {
                'helper error': json.dumps({'ok': False, 'error': 'the gateway issued no launch link'}),
                'garbage': 'garbage ' + link,
                'empty': '',
                'wrong address': json.dumps({'ok': True, 'url': 'http://evil.test/#' + SECRET}),
                'ok false with link': json.dumps({'ok': False, 'url': link}),
            }
            for name, body in cases.items():
                with self.subTest(name), self.assertRaises(RuntimeError) as caught:
                    gateway_service(gw, helper(body)).launch(7000)
                self.assertNotIn(SECRET, str(caught.exception))

    def test_failing_or_missing_helper(self):
        with server({'/api/health': (200, HEALTH)}) as gw:
            with self.assertRaises(RuntimeError):
                gateway_service(gw, (sys.executable, '-c', 'import sys; sys.exit(3)')).launch(7000)
            with self.assertRaises(RuntimeError):
                gateway_service(gw, (str(ROOT / 'no-such-interpreter.exe'),)).launch(7000)

    def test_gateway_must_be_running_and_configured(self):
        with self.assertRaises(RuntimeError):
            gateway_service(free_port(), helper('{}')).launch(7000)
        with self.assertRaises(RuntimeError):
            gateway_service(free_port()).launch(7000)
        legacy = modes.ModeService(modes.ModeConfig(url='http://127.0.0.1:18790/'))
        with self.assertRaises(RuntimeError):
            legacy.launch(7000)


class LaunchRouteTests(unittest.TestCase):
    def client(self, address='127.0.0.1', admin=True, user='fixture', **extra):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        app = FastAPI()
        app.state.auth_manager = Mock(is_configured=True)
        app.state.auth_manager.is_admin.return_value = admin

        @app.middleware('http')
        async def identity(request, call_next):
            request.state.current_user = user
            return await call_next(request)
        service = Mock()
        service.status.return_value = {'enabled': True, 'state': 'ready', 'backend': 'gateway'}
        service.launch.return_value = 'http://hermes.localhost:17000/#launch=' + SECRET
        service.grants = None                  # grants off: the legacy contract (the gateway link itself) is unchanged
        app.include_router(modes.make_router(service))
        return TestClient(app, base_url='http://127.0.0.1:17000', client=(address, 40000)), service

    ok = {'Origin': 'http://127.0.0.1:17000'}

    def test_authenticated_same_origin_administrator_gets_the_link_uncached(self):
        client, service = self.client()
        response = client.post('/api/application-modes/projects/launch', headers=self.ok)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {'url': 'http://hermes.localhost:17000/#launch=' + SECRET})
        self.assertIn('no-store', response.headers['cache-control'])
        self.assertEqual(response.headers['referrer-policy'], 'no-referrer')
        service.launch.assert_called_once_with(17000)

    def test_every_other_caller_is_refused_before_a_link_is_minted(self):
        cases = [
            ('no origin', dict(), {}),
            ('foreign origin', dict(), {'Origin': 'https://evil.example'}),
            ('other port origin', dict(), {'Origin': 'http://127.0.0.1:7000'}),
            ('null origin', dict(), {'Origin': 'null'}),
            ('remote client', dict(address='192.0.2.4'), self.ok),
            ('non-admin', dict(admin=False), self.ok),
            ('api identity', dict(user='api'), self.ok),
            ('internal tool', dict(user='internal-tool'), self.ok),
            ('bearer token', dict(), {**self.ok, 'Authorization': 'Bearer x'}),
            ('forwarded', dict(), {**self.ok, 'X-Forwarded-For': '1.2.3.4'}),
            ('foreign host', dict(), {**self.ok, 'Host': 'attacker.example:17000'}),
        ]
        for name, kwargs, headers in cases:
            client, service = self.client(**kwargs)
            with self.subTest(name):
                self.assertEqual(client.post('/api/application-modes/projects/launch', headers=headers).status_code, 403)
                service.launch.assert_not_called()

    def test_get_is_not_allowed_so_a_navigation_cannot_mint_a_link(self):
        client, service = self.client()
        self.assertEqual(client.get('/api/application-modes/projects/launch', headers=self.ok).status_code, 405)
        service.launch.assert_not_called()

    def test_service_refusal_is_a_409_without_the_link(self):
        client, service = self.client()
        service.launch.side_effect = RuntimeError('The official Hermes Webapp is not running; start it first')
        response = client.post('/api/application-modes/projects/launch', headers=self.ok)
        self.assertEqual(response.status_code, 409)
        self.assertNotIn(SECRET, response.text)

    def test_gateway_backend_offers_no_hermes_stop_or_restart(self):
        client, _ = self.client()
        available = client.get('/api/application-modes').json()['controls']
        self.assertFalse(available['hermes_stop'])
        self.assertFalse(available['hermes_restart'])


if __name__ == '__main__':
    unittest.main()

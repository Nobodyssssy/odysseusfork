"""Focused integration tests: no app import, no live state or credentials."""
import importlib.util
from pathlib import Path
import sys
import unittest
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import threading


@contextmanager
def health_server(payload=None, status=200):
    payload = payload if payload is not None else {'status': 'ok', 'sessions': 0, 'server_started_at': 123, 'active_runs': 0}
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            body = json.dumps(payload).encode()
            self.send_response(status)
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        def log_message(self, *args):
            pass
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f'http://127.0.0.1:{server.server_port}/'
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))


class ModeTests(unittest.TestCase):
    def test_mode_is_disabled_without_explicit_configuration(self):
        self.assertIsNotNone(importlib.util.find_spec('src.application_modes'), 'application mode service is not implemented')
        from src.application_modes import ModeService
        self.assertEqual(ModeService(None).status(), {'enabled': False})


    def test_existing_hermes_health_is_recognized(self):
        import src.application_modes as modes
        self.assertTrue(hasattr(modes, 'ModeConfig'), 'missing opt-in Hermes configuration')
        with health_server() as url:
            service = modes.ModeService(modes.ModeConfig(url=url))
            result = service.status()
            self.assertTrue(result['enabled'])
            self.assertEqual(result['state'], 'ready')
            self.assertEqual(result['url'], url)
            self.assertFalse(result['owned'])


    def test_remote_or_ambiguous_targets_are_rejected(self):
        from src.application_modes import ModeConfig
        for url in ['https://example.com/', 'http://localhost:8790/', 'http://127.0.0.1.evil:8790/', 'http://user@127.0.0.1:8790/', 'http://127.0.0.1:8790/hermes/', 'http://127.0.0.1:8790/?next=evil', 'http://127.0.0.1:8790/#evil', 'http://127.0.0.1:0/']:
            with self.subTest(url=url), self.assertRaises(ValueError):
                ModeConfig(url=url)

    def test_foreign_listener_is_not_hermes(self):
        from src.application_modes import ModeConfig, ModeService
        with health_server({'status': 'ok'}) as url:
            self.assertEqual(ModeService(ModeConfig(url=url)).status()['state'], 'conflict')

    def test_degraded_hermes_is_not_a_free_port(self):
        from src.application_modes import ModeConfig, ModeService
        with health_server({'status': 'degraded', 'sessions': 0, 'server_started_at': 123}, status=503) as url:
            self.assertEqual(ModeService(ModeConfig(url=url)).status()['state'], 'unavailable')

    def test_absent_hermes_is_reported_without_raising(self):
        import socket
        from src.application_modes import ModeConfig, ModeService
        with socket.socket() as sock:
            sock.bind(('127.0.0.1', 0))
            port = sock.getsockname()[1]
        self.assertEqual(ModeService(ModeConfig(url=f'http://127.0.0.1:{port}/')).status()['state'], 'stopped')


    def test_start_reuses_an_existing_active_instance(self):
        from src.application_modes import ModeConfig, ModeService
        self.assertTrue(hasattr(ModeService, 'start'), 'safe startup management is missing')
        with health_server({'status': 'ok', 'sessions': 2, 'server_started_at': 123, 'active_runs': 1}) as url:
            service = ModeService(ModeConfig(url=url))
            result = service.start()
            self.assertEqual(result['state'], 'ready')
            self.assertEqual(result['active_runs'], 1)
            self.assertFalse(result['owned'])

    def test_start_refuses_a_foreign_or_degraded_listener(self):
        from src.application_modes import ModeConfig, ModeService
        self.assertTrue(hasattr(ModeService, 'start'), 'safe startup management is missing')
        for payload, status in [({'status': 'ok'}, 200), ({'status': 'degraded', 'sessions': 0, 'server_started_at': 123}, 503)]:
            with health_server(payload, status) as url:
                with self.assertRaises(RuntimeError):
                    ModeService(ModeConfig(url=url)).start()


    def test_concurrent_start_spawns_once_and_preserves_write_policy(self):
        from unittest.mock import Mock, patch
        import os
        import tempfile
        from src.application_modes import ModeConfig, ModeService
        self.assertIn('command', ModeConfig.__dataclass_fields__, 'optional launcher is missing')
        parent = ROOT / 'data' / 'mode-tests'
        parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=parent) as temp:
            environment = dict(os.environ)
            service = ModeService(ModeConfig(url='http://127.0.0.1:18790/', command=('fixed-launcher',), environment=environment, log_dir=Path(temp)))
            process = Mock()
            process.poll.return_value = None
            with patch.object(service, 'status', return_value={'enabled': True, 'state': 'stopped'}), patch('src.application_modes.subprocess.Popen', return_value=process) as spawn:
                results = []
                threads = [threading.Thread(target=lambda: results.append(service.start())) for _ in range(5)]
                for thread in threads:
                    thread.start()
                for thread in threads:
                    thread.join()
                self.assertEqual(len(results), 5)
                self.assertEqual(spawn.call_count, 1)
                self.assertEqual(spawn.call_args.kwargs['env'].get('HERMES_WRITE_SAFE_ROOT'), os.environ.get('HERMES_WRITE_SAFE_ROOT'))
                self.assertFalse(spawn.call_args.kwargs.get('shell', False))
                process.terminate.assert_not_called()
                process.kill.assert_not_called()


    def test_environment_is_opt_in_and_does_not_expand_permissions(self):
        from src.application_modes import ModeConfig
        self.assertTrue(hasattr(ModeConfig, 'from_environment'), 'mode environment resolver is missing')
        self.assertIsNone(ModeConfig.from_environment({}))
        environment = {'ODYSSEUS_HERMES_MODE_URL': 'http://127.0.0.1:18790/', 'ODYSSEUS_HERMES_ALLOW_START': '1', 'ODYSSEUS_HERMES_REPO': str(ROOT), 'ODYSSEUS_DATA_DIR': str(ROOT / 'data'), 'ODYSSEUS_HERMES_HOME': str(ROOT / 'data/hermes-fixture'), 'HERMES_WRITE_SAFE_ROOT': 'unchanged-policy', 'HERMES_WEBUI_PYTHON': 'existing-python.exe'}
        config = ModeConfig.from_environment(environment)
        self.assertEqual(config.environment['HERMES_WRITE_SAFE_ROOT'], 'unchanged-policy')
        self.assertEqual(config.environment['HERMES_WEBUI_PYTHON'], 'existing-python.exe')
        self.assertEqual(config.environment['HERMES_HOME'], str(ROOT / 'data/hermes-fixture'))
        self.assertIn('127.0.0.1', config.command)
        self.assertIn('18790', config.command)
        self.assertEqual(environment.get('HERMES_HOME'), None)


if __name__ == '__main__':
    unittest.main()

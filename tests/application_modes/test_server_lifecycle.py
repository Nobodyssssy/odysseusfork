"""Opt-in real managed Hermes restart test; never targets production listeners."""
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import threading
import time
import unittest
from urllib.request import ProxyHandler, Request, build_opener

ROOT = Path(__file__).resolve().parents[2]


@unittest.skipUnless(os.environ.get('ODYSSEUS_HERMES_SERVER_E2E_ROOT'), 'requires the explicit managed fixture root')
class ServerLifecycleTests(unittest.TestCase):
    def test_managed_server_restart_preserves_session_draft_and_return_extension(self):
        import psutil
        from src.hermes_component import atomic_json, read_config, register_return_extension
        root = Path(os.environ['ODYSSEUS_HERMES_SERVER_E2E_ROOT']).resolve()
        config_path = root / 'managed/component.json'
        config = read_config(config_path)
        if not isinstance(config, dict):
            self.fail('The explicit fixture root has no managed component configuration.')
        self.assertEqual(config['origin'], 'managed')
        self.assertTrue(Path(config['agent']).is_relative_to(root / 'managed/releases'))
        self.assertTrue(Path(config['home']).is_relative_to(root))
        with socket.socket() as reservation:
            reservation.bind(('127.0.0.1', 0))
            port = reservation.getsockname()[1]
        self.assertNotIn(port, (7000, 8787, 17000, 18790))
        config['url'] = f'http://127.0.0.1:{port}/'
        atomic_json(config_path, config)
        register_return_extension(config['state_dir'], ROOT / 'integrations/hermes-mode', config['return_url'])
        environment = dict(os.environ)
        environment.update({'HERMES_HOME': config['home'],
                            'HERMES_WEBUI_TEST_NETWORK_BLOCK': '1', 'HF_HUB_OFFLINE': '1',
                            'TRANSFORMERS_OFFLINE': '1'})
        opener = build_opener(ProxyHandler({}))
        log_path = root / 'managed/server-e2e.log'
        process = None

        def request(path, body=None):
            data = None if body is None else json.dumps(body).encode()
            req = Request(config['url'] + path.lstrip('/'), data=data,
                          headers={'Content-Type': 'application/json', 'Origin': config['url'].rstrip('/')})
            with opener.open(req, timeout=10) as response:
                return json.load(response)

        def start():
            with log_path.open('ab') as log:
                child = subprocess.Popen([sys.executable, '-B', str(ROOT / 'scripts/hermes-component.py'), 'serve', '--root', str(root)],
                                         env=environment, stdin=subprocess.DEVNULL, stdout=log, stderr=log,
                                         creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            deadline = time.monotonic() + 90
            while time.monotonic() < deadline:
                if child.poll() is not None:
                    self.fail('Fixture backend exited; inspect its private server-e2e.log')
                try:
                    if request('/health').get('status') == 'ok':
                        return child
                except OSError:
                    pass
                threading.Event().wait(0.25)
            stop(child)
            self.fail('Fixture backend did not become healthy; inspect its private log')

        def stop(child):
            if child is None or child.poll() is not None:
                return
            owner = psutil.Process(child.pid)
            descendants = [(p.pid, p.create_time()) for p in owner.children(recursive=True)]
            # Only processes in this freshly launched, private fixture tree are touched.
            owner.terminate()
            child.wait(timeout=10)
            remaining = []
            for pid, created in descendants:
                try:
                    candidate = psutil.Process(pid)
                    if candidate.create_time() == created:
                        candidate.terminate()
                        remaining.append(candidate)
                except psutil.NoSuchProcess:
                    pass
            _, alive = psutil.wait_procs(remaining, timeout=10)
            for candidate in alive:
                candidate.kill()
            psutil.wait_procs(alive, timeout=10)

        try:
            process = start()
            listener = next(c for c in psutil.net_connections('tcp') if c.status == 'LISTEN' and c.laddr.port == port)
            server_environment = psutil.Process(listener.pid).environ()
            self.assertEqual(Path(server_environment['HERMES_WEBUI_STATE_DIR']).resolve(), Path(config['state_dir']).resolve())
            self.assertEqual(Path(server_environment['HERMES_HOME']).resolve(), Path(config['home']).resolve())
            session = request('/api/session/new', {'worktree': False})['session']
            sid = session['session_id']
            text = 'Persisted fixture draft; no model prompt was sent.'
            request('/api/session/draft', {'session_id': sid, 'text': text, 'files': []})
            with opener.open(config['url'], timeout=10) as response:
                shell = response.read().decode('utf-8')
                self.assertIn('src="extensions/odysseus-mode/return-to-odysseus.js"', shell)
                self.assertEqual(response.headers.get('X-Frame-Options'), 'DENY')
            stop(process)
            process = None
            process = start()
            self.assertEqual(request('/api/session?session_id=' + sid)['session']['composer_draft']['text'], text)
            evidence = {'port': port, 'root': str(root), 'session_id': sid,
                        'session_draft_survived_restart': True, 'return_extension_injected': True,
                        'framing_protection_preserved': True, 'state_paths_verified': True}
            atomic_json(root / 'managed/server-lifecycle-evidence.json', evidence)
        finally:
            stop(process)


if __name__ == '__main__':
    unittest.main()

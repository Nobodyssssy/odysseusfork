"""The launcher reuses real health fixtures and never takes over foreign ports."""
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from test_modes import health_server

ROOT = Path(__file__).resolve().parents[2]


class RuntimeTests(unittest.TestCase):
    def exercise(self, payload, should_reuse):
        self.assertIsNotNone(importlib.util.find_spec('src.hermes_component_runtime'), 'component runtime launcher is missing')
        from src.hermes_component_runtime import serve_component
        from src.hermes_component import ComponentError
        parent = ROOT / '.release-tests'
        parent.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=parent) as directory, health_server(payload) as url:
            root = Path(directory)
            config = root / 'component.json'
            config.write_text(json.dumps({'version': 1, 'enabled': True, 'url': url}))
            manager = Mock(config_path=config)
            with patch('src.hermes_component_runtime.ComponentManager', return_value=manager), patch('src.hermes_component_runtime.subprocess.run', side_effect=AssertionError('must not start or stop a process')):
                if should_reuse:
                    self.assertEqual(serve_component(root, ROOT, {}), 0)
                else:
                    with self.assertRaises(ComponentError):
                        serve_component(root, ROOT, {})

    def test_running_active_hermes_is_reused_without_process_actions(self):
        self.exercise({'status': 'ok', 'sessions': 3, 'server_started_at': 123, 'active_runs': 1}, True)

    def test_foreign_listener_is_not_replaced(self):
        self.exercise({'status': 'ok'}, False)


if __name__ == '__main__':
    unittest.main()

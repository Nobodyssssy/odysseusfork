"""Production mode configuration is read-only and preserves the operator policy."""
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]


class ManagedModeTests(unittest.TestCase):
    def test_saved_component_supplies_startup_without_changing_operator_environment(self):
        from src.application_modes import ModeConfig
        parent = ROOT / '.release-tests'
        parent.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=parent) as directory:
            root = Path(directory)
            (root / 'managed').mkdir()
            config = {'version': 1, 'enabled': True, 'url': 'http://127.0.0.1:18879/',
                      'webui': str(root / 'webui'), 'agent': str(root / 'agent'), 'python': sys.executable,
                      'home': str(root / 'home'), 'state_dir': str(root / 'state')}
            (root / 'managed/component.json').write_text(json.dumps(config))
            environment = {'ODYSSEUS_HERMES_COMPONENT_ROOT': str(root), 'HERMES_WRITE_SAFE_ROOT': 'operator-policy',
                           'HERMES_WEBUI_PYTHON': 'operator-bootstrap-python'}
            before = dict(environment)
            mode = ModeConfig.from_environment(environment)
            self.assertIsNotNone(mode, 'installed components must enable Hermes mode')
            self.assertIn('serve', mode.command)
            self.assertEqual(mode.url, config['url'])
            self.assertEqual(mode.environment['HERMES_WRITE_SAFE_ROOT'], 'operator-policy')
            self.assertEqual(mode.environment['HERMES_WEBUI_PYTHON'], 'operator-bootstrap-python')
            self.assertEqual(mode.environment['HERMES_HOME'], config['home'])
            self.assertEqual(environment, before)
            self.assertIsNone(ModeConfig.from_environment({**environment, 'ODYSSEUS_HERMES_DISABLED': '1'}))

    def test_saved_disable_overrides_a_legacy_manual_url(self):
        from src.application_modes import ModeConfig
        from src.hermes_component import atomic_json
        with tempfile.TemporaryDirectory(dir=ROOT / '.release-tests') as directory:
            atomic_json(Path(directory) / 'managed/component.json', {'version': 1, 'enabled': False})
            self.assertIsNone(ModeConfig.from_environment({'ODYSSEUS_HERMES_COMPONENT_ROOT': directory,
                                                         'ODYSSEUS_HERMES_MODE_URL': 'http://127.0.0.1:18879/'}))

    def test_invalid_component_never_prevents_normal_app_startup(self):
        import src.application_modes as modes
        self.assertTrue(hasattr(modes, 'create_mode_service'), 'recoverable optional-mode factory is missing')
        parent = ROOT / '.release-tests'
        parent.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=parent) as directory:
            root = Path(directory)
            (root / 'managed').mkdir()
            (root / 'managed/component.json').write_text('{broken')
            service = modes.create_mode_service({'ODYSSEUS_HERMES_COMPONENT_ROOT': str(root)})
            self.assertEqual(service.status()['state'], 'unavailable')
            with self.assertRaises(RuntimeError):
                service.start()


if __name__ == '__main__':
    unittest.main()

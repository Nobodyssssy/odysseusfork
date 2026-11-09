"""Opt-in real GitHub/PM install: isolated external state, no model downloads."""
import json
import os
from pathlib import Path
import unittest
import uuid
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]


@unittest.skipUnless(os.environ.get('ODYSSEUS_HERMES_INSTALL_E2E_ROOT'), 'real installation test requires an explicit external fixture root')
class RealInstallTests(unittest.TestCase):
    def test_fresh_install_then_idempotent_upgrade_preserves_state_and_policy(self):
        from src.hermes_component import ComponentManager
        root = Path(os.environ['ODYSSEUS_HERMES_INSTALL_E2E_ROOT']) / ('fresh-' + uuid.uuid4().hex[:8])
        manager = ComponentManager(root, application_root=ROOT)
        original_policy = os.environ.get('HERMES_WRITE_SAFE_ROOT')
        config = manager.ensure(fresh=True)
        self.assertEqual(config['origin'], 'managed')
        self.assertEqual(os.environ.get('HERMES_WRITE_SAFE_ROOT'), original_policy)
        home = Path(config['home'])
        retained = home / 'fixture-memory.txt'
        retained.write_text('user state is retained', encoding='utf-8')
        with patch('src.hermes_component.checkout_source', side_effect=AssertionError('idempotent install must not clone again')):
            second = manager.ensure()
        self.assertEqual(second, config)
        self.assertEqual(retained.read_text(), 'user state is retained')
        self.assertTrue((Path(config['state_dir']) / 'extensions/odysseus-mode/manifest.json').is_file())
        evidence = root.parent / 'last-real-install.json'
        evidence.write_text(json.dumps({'root': str(root), 'config': config, 'idempotent': True, 'policy_unchanged': True}, indent=2), encoding='utf-8')


if __name__ == '__main__':
    unittest.main()

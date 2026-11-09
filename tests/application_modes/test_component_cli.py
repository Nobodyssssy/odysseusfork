"""The installer CLI does not start services for status/disable operations."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]


class ComponentCliTests(unittest.TestCase):
    def test_cli_reports_missing_and_preserves_disabled_setting(self):
        script = ROOT / 'scripts/hermes-component.py'
        self.assertTrue(script.is_file(), 'Windows component CLI is missing')
        with tempfile.TemporaryDirectory(dir=os.environ.get('ODYSSEUS_COMPONENT_TEST_ROOT')) as directory:
            root = Path(directory) / 'component'
            def run(action):
                result = subprocess.run([sys.executable, '-B', str(script), action, '--root', str(root)],
                                        capture_output=True, text=True, timeout=30)
                self.assertEqual(result.returncode, 0, result.stderr)
                return json.loads(result.stdout)
            self.assertEqual(run('status')['state'], 'not_installed')
            self.assertFalse(root.exists(), 'status must not create component state')
            self.assertFalse(run('disable')['enabled'])
            self.assertFalse(run('ensure')['enabled'])
            self.assertFalse((root / 'managed/releases').exists(), 'disabled components must not download anything')


if __name__ == '__main__':
    unittest.main()

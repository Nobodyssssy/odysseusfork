"""The reviewed WebUI patch applies to a fresh checkout of the pin, exactly once, and nothing else is accepted."""
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

from src.hermes_component import (ComponentError, SourcePin, apply_patch, load_pins, patch_file, patch_state,
                                  verify_checkout)

ROOT = Path(__file__).resolve().parents[2]
UPSTREAM = Path(os.environ.get('HERMES_WEBUI_SOURCE', r'F:\hermes-webui'))


@unittest.skipUnless((UPSTREAM / '.git').exists(), 'needs a local clone of nesquena/hermes-webui')
class PatchTests(unittest.TestCase):
    def setUp(self):
        self.pin = load_pins(ROOT)['webui']
        self.patch = patch_file(ROOT, 'webui')
        self.assertIsNotNone(self.patch)
        self.tmp = tempfile.TemporaryDirectory(dir=os.environ.get('HERMES_TEST_TMP'))
        self.addCleanup(self.tmp.cleanup)
        self.checkout = Path(self.tmp.name) / 'webui'
        for args in (['clone', '--no-checkout', '-q', str(UPSTREAM), str(self.checkout)],
                     ['-C', str(self.checkout), '-c', 'core.autocrlf=true', 'checkout', '-q', '--detach', self.pin.revision]):
            subprocess.run(['git', *args], check=True, capture_output=True)

    def test_clean_pin_then_patched_then_idempotent(self):
        self.assertEqual(patch_state(self.checkout, self.patch), 'absent')
        verify_checkout(self.checkout, self.pin, self.patch)
        self.assertEqual(apply_patch(self.checkout, self.pin, self.patch), 'applied')
        self.assertEqual(patch_state(self.checkout, self.patch), 'applied')
        self.assertEqual(apply_patch(self.checkout, self.pin, self.patch), 'applied')       # a second run changes nothing
        self.assertTrue((self.checkout / 'api/trusted_workspaces.py').is_file())
        verify_checkout(self.checkout, self.pin, self.patch)
        with self.assertRaises(ComponentError):
            verify_checkout(self.checkout, self.pin)                                          # without the patch it is "foreign"

    def test_a_foreign_edit_is_refused_and_never_overwritten(self):
        target = self.checkout / 'server.py'
        original = target.read_bytes()
        target.write_bytes(original + b'\n# local edit\n')
        with self.assertRaises(ComponentError):
            apply_patch(self.checkout, self.pin, self.patch)
        self.assertEqual(target.read_bytes(), original + b'\n# local edit\n')

    def test_extra_edit_on_top_of_the_patch_is_refused(self):
        apply_patch(self.checkout, self.pin, self.patch)
        other = self.checkout / 'README.md'
        other.write_bytes(other.read_bytes() + b'\nx\n')
        with self.assertRaises(ComponentError):
            verify_checkout(self.checkout, self.pin, self.patch)

    def test_wrong_revision_is_refused(self):
        wrong = SourcePin('webui', 'nesquena/hermes-webui', '0' * 40)
        with self.assertRaises(ComponentError):
            verify_checkout(self.checkout, wrong, self.patch)


if __name__ == '__main__':
    unittest.main()

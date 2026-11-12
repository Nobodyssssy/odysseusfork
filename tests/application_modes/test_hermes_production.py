import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from src import hermes_production as production


class Switch(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.env = Path(self.tmp.name) / '.env'

    def test_on_then_off_restores_the_original_bytes_and_keeps_a_copy(self):
        original = b'\xef\xbb\xbfA=1\r\nSECRET=x y\r\nODYSSEUS_HERMES_BACKEND=gateway\r\nB=2\r\n'
        self.env.write_bytes(original)
        result = production.switch(self.env, True)
        self.assertTrue(result['changed'] and Path(result['copy']).read_bytes() == original)
        self.assertEqual(production.current(self.env), 'webapp')
        raw = self.env.read_bytes()
        self.assertTrue(raw.startswith(b'\xef\xbb\xbf') and b'\r\n' in raw and b'SECRET=x y' in raw and raw.count(b'ODYSSEUS_HERMES_BACKEND') == 1)
        self.assertFalse(production.switch(self.env, True)['changed'])
        production.switch(self.env, False)
        self.assertIsNone(production.current(self.env))
        self.assertEqual(self.env.read_bytes(), b'\xef\xbb\xbfA=1\r\nSECRET=x y\r\nB=2\r\n')

    def test_missing_file_is_not_created(self):
        with self.assertRaises(production.ProductionError):
            production.switch(self.env, True)
        self.assertFalse(self.env.exists())


if __name__ == '__main__':
    unittest.main()

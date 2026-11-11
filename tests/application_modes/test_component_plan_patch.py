"""plan_existing (adoption/discovery) must judge an existing WebUI exactly as `serve` does: pin + the reviewed patch is
MATCHING; anything else is DIFFERING, including a stray untracked file next to the patch."""
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch as mock_patch

from src.hermes_component import (ComponentError, SourcePin, apply_patch, load_pins, patch_file, patch_state, plan_existing,
                                  verify_checkout)

ROOT = Path(__file__).resolve().parents[2]
UPSTREAM = Path(os.environ.get('HERMES_WEBUI_SOURCE', r'F:\hermes-webui'))


def git(*args, cwd=None):
    return subprocess.run(['git', *args], check=True, capture_output=True, text=True, cwd=cwd).stdout.strip()


@unittest.skipUnless((UPSTREAM / '.git').exists(), 'needs a local clone of nesquena/hermes-webui')
class PlanExistingPatchTests(unittest.TestCase):
    def setUp(self):
        pins = load_pins(ROOT)
        self.webui_pin = pins['webui']
        self.tmp = tempfile.TemporaryDirectory(dir=os.environ.get('HERMES_TEST_TMP'))
        self.addCleanup(self.tmp.cleanup)
        base = Path(self.tmp.name)
        self.webui = base / 'webui'
        git('clone', '--no-checkout', '-q', str(UPSTREAM), str(self.webui))
        git('-C', str(self.webui), '-c', 'core.autocrlf=true', 'checkout', '-q', '--detach', self.webui_pin.revision)
        self.agent = base / 'agent'
        self.agent.mkdir()
        git('init', '-q', str(self.agent))
        (self.agent / 'run_agent.py').write_text('# fixture\n')
        git('-C', str(self.agent), 'add', '.')
        git('-C', str(self.agent), '-c', 'user.name=F', '-c', 'user.email=f@example.invalid', 'commit', '-q', '-m', 'fixture')
        self.pins = {'webui': self.webui_pin, 'agent': SourcePin('agent', 'NousResearch/hermes-agent', git('-C', str(self.agent), 'rev-parse', 'HEAD'))}
        self.home = base / 'home'
        self.home.mkdir()
        self.patch = patch_file(ROOT, 'webui')

    def plan(self, with_application_root=True):
        with mock_patch('src.hermes_component.probe_python', return_value={'version': [3, 14, 9]}):
            return plan_existing(self.webui, self.agent, self.home, 'runtime-python', self.pins,
                                 application_root=ROOT if with_application_root else None)

    def patched(self):
        apply_patch(self.webui, self.webui_pin, self.patch)

    def test_pin_plus_reviewed_patch_reads_as_matching(self):
        self.patched()
        self.assertEqual(patch_state(self.webui, self.patch), 'applied')
        self.assertEqual(self.plan()['origin'], 'external')

    def test_without_the_patch_known_the_same_tree_differs_which_was_the_bug(self):
        self.patched()
        with self.assertRaises(ComponentError):
            self.plan(with_application_root=False)
        with self.assertRaises(ComponentError):
            verify_checkout(self.webui, self.webui_pin)

    def test_a_clean_pin_is_still_accepted_as_before(self):
        self.assertEqual(patch_state(self.webui, self.patch), 'absent')
        self.assertEqual(self.plan()['origin'], 'external')

    def test_an_edit_on_top_of_the_patch_differs(self):
        self.patched()
        target = self.webui / 'README.md'
        target.write_bytes(target.read_bytes() + b'\nlocal edit\n')
        with self.assertRaises(ComponentError):
            self.plan()

    def test_a_stray_untracked_file_next_to_the_patch_differs(self):
        self.patched()
        (self.webui / 'csrf_matrix.py').write_text('print("probe")\n')
        self.assertEqual(patch_state(self.webui, self.patch), 'foreign')
        with self.assertRaises(ComponentError):
            self.plan()
        with self.assertRaises(ComponentError):
            verify_checkout(self.webui, self.webui_pin, self.patch)

    def test_a_stray_untracked_file_in_a_new_folder_differs(self):
        self.patched()
        (self.webui / 'scratch').mkdir()
        (self.webui / 'scratch' / 'x.py').write_text('x = 1\n')
        with self.assertRaises(ComponentError):
            self.plan()

    def test_ignored_files_do_not_count(self):
        self.patched()
        (self.webui / 'api' / '__pycache__').mkdir(exist_ok=True)
        (self.webui / 'api' / '__pycache__' / 'x.cpython-314.pyc').write_bytes(b'\0')
        self.assertEqual(patch_state(self.webui, self.patch), 'applied')

    def test_a_partly_reverted_patch_differs(self):
        self.patched()
        git('-C', str(self.webui), 'checkout', '--', 'server.py')
        self.assertEqual(patch_state(self.webui, self.patch), 'foreign')
        with self.assertRaises(ComponentError):
            self.plan()

    def test_a_deleted_patch_file_differs(self):
        self.patched()
        (self.webui / 'api' / 'trusted_workspaces.py').unlink()
        with self.assertRaises(ComponentError):
            self.plan()

    def test_the_wrong_revision_differs(self):
        self.patched()
        wrong = dict(self.pins, webui=SourcePin('webui', 'nesquena/hermes-webui', '0' * 40))
        with mock_patch('src.hermes_component.probe_python', return_value={'version': [3, 14, 9]}), self.assertRaises(ComponentError):
            plan_existing(self.webui, self.agent, self.home, 'runtime-python', wrong, application_root=ROOT)


if __name__ == '__main__':
    unittest.main()

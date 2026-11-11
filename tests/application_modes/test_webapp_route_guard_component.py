"""Webapp component: the reviewed route guard is part of its source, and its state is backed up (grants are not)."""
import json
import subprocess
import tempfile
import types
import unittest
from pathlib import Path

from src.hermes_backup import create_backup, verify_backup
from src.hermes_component import apply_patch, patch_state, verify_checkout
from src.hermes_webapp_component import ROUTE_GUARD_PATCH, WebappComponent

ROOT = Path(__file__).resolve().parents[2]
GIT = ['git', '-c', 'user.name=t', '-c', 'user.email=t@example.invalid']


class RouteGuardComponentTests(unittest.TestCase):
    def test_guard_patch_ships_and_is_owned_by_the_webapp_component_only(self):
        import src.hermes_component as shared
        self.assertTrue((ROOT / ROUTE_GUARD_PATCH).is_file())
        self.assertNotIn('agent', shared._PATCHES, 'the WebUI component must never patch the live agent checkout')

    def test_guard_state_reflects_source_and_adopted_gateways_are_unpatched(self):
        manager = WebappComponent.__new__(WebappComponent)
        manager.application_root = ROOT
        self.assertEqual(manager.route_guard_state({'origin': 'adopted', 'agent': 'x'}), 'absent')
        self.assertEqual(manager.route_guard_state({'origin': 'managed', 'agent': str(ROOT / 'does-not-exist')}), 'unknown')

    def test_patch_state_detects_a_tampered_source(self):
        with tempfile.TemporaryDirectory(dir=ROOT / '.release-tests' if (ROOT / '.release-tests').is_dir() else None) as d:
            repo = Path(d)
            patch = repo.parent / 'x.patch'
            subprocess.run(['git', 'init', '-q', str(repo)], check=True)
            (repo / 'f.txt').write_text('a\n')
            subprocess.run([*GIT, '-C', str(repo), 'add', '.'], check=True)
            subprocess.run([*GIT, '-C', str(repo), 'commit', '-qm', 'c'], check=True)
            (repo / 'f.txt').write_text('b\n')
            patch.write_bytes(subprocess.run(['git', '-C', str(repo), 'diff'], capture_output=True, check=True).stdout)
            subprocess.run(['git', '-C', str(repo), 'checkout', '-q', 'f.txt'], check=True)
            revision = subprocess.run(['git', '-C', str(repo), 'rev-parse', 'HEAD'], capture_output=True, text=True).stdout.strip()
            pin = types.SimpleNamespace(revision=revision)
            self.assertEqual(apply_patch(repo, pin, patch), 'applied')
            (repo / 'f.txt').write_text('tampered\n')
            self.assertEqual(patch_state(repo, patch), 'foreign')
            with self.assertRaises(Exception):
                verify_checkout(repo, pin, patch)
            patch.unlink()


class BackupScopeTests(unittest.TestCase):
    def test_webapp_state_is_saved_grants_and_credentials_are_not(self):
        parent = ROOT / '.release-tests'
        parent.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=parent) as directory:
            base = Path(directory)
            ody, hermes, component = base / 'ody', base / 'hermes', base / 'component'
            (ody / 'data').mkdir(parents=True)
            hermes.mkdir()
            (ody / 'data' / 'settings.json').write_text('{}')
            (ody / 'data' / 'hermes-grants.db').write_bytes(b'not-a-real-database')
            (component / 'managed' / 'webapp').mkdir(parents=True)
            (component / 'managed' / 'webapp' / 'component.json').write_text('{"kind":"webapp"}')
            gateway = component / 'state' / 'hermes-gateway'
            (gateway / 'webapp').mkdir(parents=True)
            (gateway / 'webapp' / 'credential.json').write_text('SECRET')
            (gateway / 'memories').mkdir()
            (gateway / 'memories' / 'm.md').write_text('keep')
            backup = create_backup(ody, hermes, base / 'backups', component_root=component)
            self.assertTrue(verify_backup(backup)['verified'])
            self.assertEqual((backup / 'component/managed/webapp/component.json').read_text(), '{"kind":"webapp"}')
            self.assertEqual((backup / 'component/state/hermes-gateway/memories/m.md').read_text(), 'keep')
            self.assertFalse((backup / 'odysseus/data/hermes-grants.db').exists())
            self.assertFalse(list(backup.rglob('credential.json')))
            self.assertIn('hermes-grants.db', json.dumps(json.loads((backup / 'manifest.json').read_text())['excluded']))


if __name__ == '__main__':
    unittest.main()

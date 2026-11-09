"""Windows component tests use disposable state, never live Hermes homes."""
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]


class ComponentTests(unittest.TestCase):
    def test_disabled_component_stays_disabled_across_ensure(self):
        self.assertIsNotNone(importlib.util.find_spec('src.hermes_component'), 'component provisioning is missing')
        from src.hermes_component import ComponentManager
        parent = ROOT / '.release-tests'
        parent.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=parent) as directory:
            manager = ComponentManager(Path(directory))
            manager.disable()
            self.assertFalse(manager.ensure()['enabled'])
            self.assertFalse(json.loads(manager.config_path.read_text())['enabled'])
    def test_explicit_enable_restores_saved_component_without_reinstalling(self):
        from unittest.mock import patch
        from src.hermes_component import ComponentManager, atomic_json, read_config
        parent = ROOT / '.release-tests'
        parent.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=parent) as directory:
            manager = ComponentManager(Path(directory))
            original = {'version': 1, 'enabled': False, 'home': 'retained-home'}
            atomic_json(manager.config_path, original)
            with patch.object(manager, 'validate'), patch.object(manager, '_install_generation') as install:
                result = manager.ensure(enable=True)
            self.assertTrue(result['enabled'])
            self.assertEqual(read_config(manager.config_path), {**original, 'enabled': True})
            install.assert_not_called()

    def test_disable_preserves_component_paths_and_corrupt_config_is_not_replaced(self):
        from src.hermes_component import ComponentManager, atomic_json, ComponentError
        parent = ROOT / '.release-tests'
        parent.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=parent) as directory:
            manager = ComponentManager(Path(directory))
            atomic_json(manager.config_path, {'version': 1, 'enabled': True, 'home': 'retained-home'})
            manager.disable()
            self.assertEqual(json.loads(manager.config_path.read_text())['home'], 'retained-home')
            manager.config_path.write_text('{broken', encoding='utf-8')
            with self.assertRaises(ComponentError):
                manager.ensure()
            self.assertEqual(manager.config_path.read_text(), '{broken')

    def test_unrecognized_component_version_fails_closed(self):
        from src.hermes_component import ComponentManager, atomic_json, ComponentError
        parent = ROOT / '.release-tests'
        parent.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=parent) as directory:
            manager = ComponentManager(Path(directory))
            atomic_json(manager.config_path, {'version': 999, 'enabled': True})
            with self.assertRaises(ComponentError):
                manager.ensure()

    def test_existing_runtime_is_resolved_without_bootstrapping_or_ignoring_python_version(self):
        from src.hermes_component import probe_python, ComponentError
        import sys
        with self.assertRaises(ComponentError):
            probe_python('nonexistent-python-for-component.exe', require_agent=True)
        result = probe_python(sys.executable, require_agent=False)
        self.assertEqual(result['version'][:2], [sys.version_info.major, sys.version_info.minor])

    def test_provision_requires_git_and_does_not_create_an_unverified_checkout(self):
        from unittest.mock import patch
        import src.hermes_component as component
        self.assertTrue(hasattr(component, 'checkout_source'), 'verified source provisioning is missing')
        with patch('shutil.which', return_value=None):
            with self.assertRaises(component.ComponentError):
                component.checkout_source(Path('unused-provision-target'), component.SourcePin('agent', 'NousResearch/hermes-agent', 'a' * 40), Path('unused.log'), {})

    def test_failed_repair_keeps_last_configuration_and_existing_state(self):
        from unittest.mock import patch
        from src.hermes_component import ComponentManager, atomic_json, ComponentError
        parent = ROOT / '.release-tests'
        parent.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=parent) as directory:
            manager = ComponentManager(Path(directory))
            original = {'version': 1, 'enabled': True, 'home': 'preserved-home'}
            atomic_json(manager.config_path, original)
            with patch.object(manager, '_install_generation', create=True, side_effect=ComponentError('download failed')) as install:
                with self.assertRaises(ComponentError):
                    manager.ensure(repair=True)
                install.assert_called_once()
            self.assertEqual(json.loads(manager.config_path.read_text()), original)

    def test_adoption_plan_does_not_write_into_existing_state(self):
        from unittest.mock import patch
        from src.hermes_component import ComponentManager, SourcePin, plan_existing
        import subprocess
        parent = ROOT / '.release-tests'
        parent.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=parent) as directory:
            root = Path(directory)
            pins = {}
            for name, repository, marker in [('agent', 'NousResearch/hermes-agent', 'run_agent.py'), ('webui', 'nesquena/hermes-webui', 'server.py')]:
                repo = root / name
                repo.mkdir()
                subprocess.run(['git', 'init', '--quiet', str(repo)], check=True)
                (repo / marker).write_text('# fixture\n', encoding='utf-8')
                subprocess.run(['git', '-C', str(repo), 'add', '.'], check=True)
                subprocess.run(['git', '-C', str(repo), '-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.invalid', 'commit', '--quiet', '-m', 'fixture'], check=True)
                revision = subprocess.check_output(['git', '-C', str(repo), 'rev-parse', 'HEAD'], text=True).strip()
                pins[name] = SourcePin(name, repository, revision)
            home = root / 'existing-home'
            home.mkdir()
            (home / 'memory.txt').write_text('preserve me', encoding='utf-8')
            with patch('src.hermes_component.probe_python', return_value={'version': [3, 14, 9]}) as probe:
                plan = plan_existing(root / 'webui', root / 'agent', home, 'runtime-python', pins)
            self.assertEqual(plan['home'], str(home.resolve()))
            self.assertEqual(plan['origin'], 'external')
            self.assertEqual(list(home.iterdir()), [home / 'memory.txt'])
            probe.assert_called_once_with('runtime-python', require_agent=True)
            self.assertFalse(ComponentManager(root / 'component').config_path.exists())

    def test_pins_reject_mutable_revisions_and_foreign_repositories(self):
        from src.hermes_component import SourcePin, ComponentError
        for repository, revision in [('NousResearch/hermes-agent', 'main'), ('evil/agent', 'a' * 40)]:
            with self.subTest(repository=repository, revision=revision), self.assertRaises(ComponentError):
                SourcePin('agent', repository, revision)

    def test_checkout_validation_detects_changed_tracked_source(self):
        import subprocess
        from src.hermes_component import SourcePin, verify_checkout, ComponentError
        parent = ROOT / '.release-tests'
        parent.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=parent) as directory:
            repo = Path(directory)
            def git(*args):
                return subprocess.check_output(['git', '-C', str(repo), *args], text=True).strip()
            git('init', '--quiet')
            git('config', 'user.name', 'Fixture')
            git('config', 'user.email', 'fixture@example.invalid')
            (repo / 'run_agent.py').write_text('# fixture\n', encoding='utf-8')
            git('add', 'run_agent.py')
            git('commit', '--quiet', '-m', 'fixture')
            pin = SourcePin('agent', 'NousResearch/hermes-agent', git('rev-parse', 'HEAD'))
            verify_checkout(repo, pin)
            (repo / 'run_agent.py').write_text('# modified\n', encoding='utf-8')
            with self.assertRaises(ComponentError):
                verify_checkout(repo, pin)


if __name__ == '__main__':
    unittest.main()

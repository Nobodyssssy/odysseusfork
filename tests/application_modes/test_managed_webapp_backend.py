"""Projects mode over the managed Webapp component: start on demand, never touch adopted or foreign processes, never restart on a mode switch."""
from pathlib import Path
import sys
import threading
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

import src.application_modes as modes
from src import service_controls as controls


class FakeComponent:
    def __init__(self, state='stopped', sessions=0, start_error=None):
        self.state, self.sessions, self.start_error = state, sessions, start_error
        self.calls = []
        self.release = threading.Event()
        self.release.set()

    def status(self):
        return {'state': self.state, 'route_guard': 'applied', 'revision': 'abc'}

    def active_sessions(self):
        return self.sessions

    def start(self, wait=True, timeout=180):
        self.calls.append('start')
        self.release.wait(5)
        if self.start_error:
            raise RuntimeError(self.start_error)
        self.state = 'ready'

    def stop(self, force=False):
        self.calls.append(('stop', force))
        if self.sessions and not force:
            raise controls.ActiveRuns(self.sessions)
        self.state = 'stopped'


def service(component, ownership='managed'):
    config = modes.ModeConfig(url='http://127.0.0.1:19130/', backend='gateway', launcher=('x',), component_root=Path('unused'), ownership=ownership)
    svc = modes.ModeService(config)
    svc._component_cache = component
    return svc


def wait_for(predicate, seconds=3):
    import time
    end = time.monotonic() + seconds
    while time.monotonic() < end and not predicate():
        time.sleep(0.02)
    return predicate()


class ManagedBackendTests(unittest.TestCase):
    def test_stopped_gateway_is_started_on_demand_and_reports_starting_then_ready(self):
        fake = FakeComponent('stopped')
        fake.release.clear()
        svc = service(fake)
        self.assertEqual(svc.status()['state'], 'stopped')
        self.assertEqual(svc.start()['state'], 'starting')
        self.assertEqual(svc.status()['state'], 'starting')
        self.assertEqual(svc.start()['state'], 'starting')              # a second click does not start a second gateway
        fake.release.set()
        self.assertTrue(wait_for(lambda: svc.status()['state'] == 'ready'))
        self.assertEqual(fake.calls, ['start'])

    def test_ready_gateway_is_reused_never_restarted_by_switching_modes(self):
        fake = FakeComponent('ready', sessions=2)
        svc = service(fake)
        status = svc.start()
        self.assertEqual((status['state'], status['busy'], status['active_runs']), ('ready', True, 2))
        self.assertEqual(fake.calls, [])

    def test_failed_start_is_reported_as_an_error_not_hidden(self):
        fake = FakeComponent('stopped', start_error='The gateway exited while starting; see the log.')
        svc = service(fake)
        svc.start()
        self.assertTrue(wait_for(lambda: svc.status()['state'] == 'error'))
        self.assertIn('exited while starting', svc.status()['error'])
        fake.start_error = None                                         # a later click can recover
        svc.start()
        self.assertTrue(wait_for(lambda: svc.status()['state'] == 'ready'))
        self.assertIsNone(svc.status().get('error'))

    def test_adopted_and_foreign_listeners_are_never_started_stopped_or_restarted(self):
        adopted = FakeComponent('stopped')
        svc = service(adopted, ownership='adopted')
        with self.assertRaises(RuntimeError):
            svc.start()
        with self.assertRaises(controls.Unavailable):
            svc.stop()
        with self.assertRaises(controls.Unavailable):
            svc.restart()
        self.assertEqual(adopted.calls, [])
        self.assertFalse(svc.can_start())
        foreign = FakeComponent('conflict')
        managed = service(foreign)
        with self.assertRaises(RuntimeError):
            managed.start()
        self.assertEqual(foreign.calls, [])

    def test_stop_refuses_active_runs_unless_forced_and_restart_goes_through_the_same_check(self):
        fake = FakeComponent('ready', sessions=1)
        svc = service(fake)
        with self.assertRaises(controls.ActiveRuns):
            svc.stop()
        self.assertEqual(fake.state, 'ready')
        self.assertEqual(svc.stop(force=True)['state'], 'stopped')

    def test_cannot_stop_while_starting(self):
        fake = FakeComponent('stopped')
        fake.release.clear()
        svc = service(fake)
        svc.start()
        with self.assertRaises(controls.Unavailable):
            svc.stop()
        fake.release.set()

    def test_environment_selects_the_component_and_carries_ownership(self):
        import tempfile
        outside = tempfile.mkdtemp()
        self.addCleanup(__import__('shutil').rmtree, outside, True)
        saved = {'enabled': True, 'url': 'http://127.0.0.1:19130/', 'home': outside, 'agent': outside, 'python': sys.executable, 'origin': 'adopted'}
        with patch('src.hermes_webapp_component.WebappComponent.config', return_value=saved), patch('src.runtime_paths.get_hermes_component_root', return_value=outside):
            config = modes.ModeConfig.from_environment({'ODYSSEUS_HERMES_BACKEND': 'webapp'})
            assert config is not None
            self.assertEqual((config.backend, config.ownership, config.url), ('gateway', 'adopted', 'http://127.0.0.1:19130/'))
            self.assertTrue(config.launcher and config.component_root)
        with patch('src.hermes_webapp_component.WebappComponent.config', return_value=None), patch('src.runtime_paths.get_hermes_component_root', return_value=outside):
            self.assertIsNone(modes.ModeConfig.from_environment({'ODYSSEUS_HERMES_BACKEND': 'webapp'}))
        with patch('src.hermes_webapp_component.WebappComponent.config', return_value={**saved, 'enabled': False}), patch('src.runtime_paths.get_hermes_component_root', return_value=outside):
            self.assertIsNone(modes.ModeConfig.from_environment({'ODYSSEUS_HERMES_BACKEND': 'webapp'}))


class BlockedFoldersTests(unittest.TestCase):
    def test_neutral_list_imports_the_legacy_list_once_and_is_what_the_guard_reads(self):
        import json
        import tempfile
        from src.hermes_webapp_component import WebappComponent
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory) / 'home'
            (home / 'vault').mkdir(parents=True)
            (home / 'vault' / 'webui-trusted-workspaces.json').write_text(json.dumps({'version': 1, 'blocked': [{'path': 'F:\\old\\secrets'}]}))
            component = WebappComponent.__new__(WebappComponent)
            component.application_root = ROOT
            config = {'origin': 'managed', 'home': str(home), 'enabled': True}
            component.config = lambda: config
            component._require_managed = lambda: config
            self.assertEqual(component.blocked_folders(), ['F:\\old\\secrets'])               # legacy list still honoured before migration
            component.set_blocked_folders(['F:\\new\\private'])
            neutral = json.loads((home / 'vault' / 'odysseus-protected-paths.json').read_text())
            self.assertEqual([item['path'] for item in neutral['blocked']], ['F:\\old\\secrets', 'F:\\new\\private'])
            component.remove_blocked_folder('F:\\old\\secrets')
            self.assertEqual(component.blocked_folders(), ['F:\\new\\private'])
            config['origin'] = 'adopted'
            with self.assertRaises(Exception):
                component.set_blocked_folders(['x'])                                           # an adopted home is never written


if __name__ == '__main__':
    unittest.main()

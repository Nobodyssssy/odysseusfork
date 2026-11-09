"""Behaviour of provider sharing: both directions, opt-in, deletion, conflicts, persistence, loops, secrets."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from src.provider_catalog import canonical_url
from src.provider_sync import ProviderSync, SyncError

SECRET = 'sk-SENTINEL-must-never-appear-0123456789'
URL = 'http://localhost:8000/v1'


def od_row(**kw):
    base = dict(id='e1', name='Local llama', base_url=URL, api_key=None, is_enabled=True,
                cached_models='["ornith-gguf"]', pinned_models=None, hidden_models=None, provider_auth_id=None)
    base.update(kw)
    return SimpleNamespace(**base)


def he_item(**kw):
    base = dict(id='custom:ody-1', display_name='Local llama', has_key=False, is_oauth=False, is_self_hosted=True,
                base_url='http://127.0.0.1:8000/v1', models=[{'id': 'ornith-gguf'}], auth_error=None)
    base.update(kw)
    return base


class World:
    """Both applications' provider stores, behind the same seams the live service uses."""
    def __init__(self, odysseus=(), hermes=(), hermes_state='ready', probe=('offline', ())):
        self.odysseus, self.hermes, self.hermes_state, self.probe = list(odysseus), list(hermes), hermes_state, probe
        self.state, self.writes = {}, []
        self.invalidated = 0
        self.sync = self.new_sync()

    def new_sync(self):
        return ProviderSync(
            load_state=lambda: json.loads(json.dumps(self.state)), save_state=lambda d: self.state.update(json.loads(json.dumps(d))) or None,
            odysseus_rows=lambda: list(self.odysseus), hermes_fetch=lambda: ({'providers': list(self.hermes)} if self.hermes_state == 'ready' else None, self.hermes_state),
            create_odysseus=self.create_odysseus, append_hermes=self.append_hermes, probe=lambda url: self.probe,
            clock=lambda: 0, on_odysseus_change=lambda: setattr(self, 'invalidated', self.invalidated + 1))

    def create_odysseus(self, definition):
        self.writes.append(('odysseus', dict(definition)))
        if any(canonical_url(r.base_url) == canonical_url(definition['base_url']) for r in self.odysseus):
            return 'present'
        self.odysseus.append(od_row(id='sync-' + definition['slug'], name=definition['name'], base_url=definition['base_url'],
                                    cached_models=json.dumps(definition['models'])))
        return 'created'

    def append_hermes(self, definition):
        self.writes.append(('hermes', dict(definition)))
        if any(canonical_url(h['base_url']) == canonical_url(definition['base_url']) for h in self.hermes if h.get('base_url')):
            return 'present'
        self.hermes.append(he_item(id='custom:' + definition['slug'], display_name=definition['name'], base_url=definition['base_url'],
                                   models=[{'id': m} for m in definition['models']]))
        return 'added'

    def row(self, refresh=True):
        return self.sync.snapshot(refresh=refresh)['providers']


class SyncTests(unittest.TestCase):
    def test_odysseus_to_hermes_requires_opt_in_then_mirrors_once_without_credentials(self):
        world = World(odysseus=[od_row()])
        identity = world.row()[0]['id']
        with self.assertRaises(SyncError):
            world.sync.apply(identity)                       # not opted in: nothing happens
        self.assertEqual(world.writes, [])
        world.sync.set_share(identity, True)
        self.assertEqual(world.row()[0]['sync'], 'pending_hermes')
        self.assertEqual(world.sync.apply(identity)['result'], 'added')
        self.assertEqual(world.sync.apply(identity)['result'], 'already_present')
        self.assertEqual([w[0] for w in world.writes], ['hermes'])
        self.assertEqual(set(world.writes[0][1]), {'name', 'base_url', 'models', 'slug'})
        row = world.row()[0]
        self.assertEqual((row['source'], row['sync']), ('both', 'in_sync'))

    def test_hermes_to_odysseus_creates_a_keyless_endpoint_and_refreshes_the_picker(self):
        world = World(hermes=[he_item(id='lmstudio', display_name='LM Studio')])
        identity = world.row()[0]['id']
        world.sync.set_share(identity, True)
        self.assertEqual(world.sync.apply(identity), {'result': 'created', 'id': identity, 'target': 'odysseus'})
        created = world.odysseus[0]
        self.assertIsNone(created.api_key)
        self.assertEqual(json.loads(created.cached_models), ['ornith-gguf'])
        self.assertEqual(world.invalidated, 1)
        self.assertEqual(world.row()[0]['sync'], 'in_sync')

    def test_a_keyed_cloud_provider_mirrors_without_its_key_and_asks_for_authentication(self):
        remote = od_row(base_url='https://api.example.test/v1', api_key=SECRET, name='Cloud')
        world = World(odysseus=[remote], hermes=[he_item(id='lmstudio', base_url=None, has_key=False, is_self_hosted=False)])
        identity = world.row()[0]['id']
        world.sync.set_share(identity, True)
        world.sync.apply(identity)
        self.assertNotIn(SECRET, json.dumps(world.writes) + json.dumps(world.state) + json.dumps(world.row()))
        mirrored = world.hermes[-1]
        self.assertFalse(mirrored['has_key'])
        status = world.row()[0]['status']
        self.assertEqual(status['hermes'], 'requires_authentication')
        self.assertEqual(status['odysseus'], 'connected')

    def test_oauth_and_subscription_providers_cannot_be_shared_at_all(self):
        world = World(hermes=[he_item(id='openai-codex', display_name='Codex', base_url=None, is_self_hosted=False, is_oauth=True, has_key=True)])
        identity = world.row()[0]['id']
        with self.assertRaises(SyncError):
            world.sync.set_share(identity, True)
        with self.assertRaises(SyncError):
            world.sync.apply(identity)
        self.assertEqual(world.writes, [])

    def test_builtin_hermes_services_are_not_mirrored_as_keyless_duplicates(self):
        world = World(odysseus=[od_row(base_url='https://openrouter.ai/api/v1', api_key=SECRET, name='OpenRouter')])
        identity = world.row()[0]['id']
        world.sync.set_share(identity, True)
        with self.assertRaisesRegex(SyncError, 'built-in'):
            world.sync.apply(identity)
        self.assertEqual(world.writes, [])

    def test_a_mirror_deleted_by_the_user_is_never_recreated_until_sharing_is_re_enabled(self):
        world = World(odysseus=[od_row()])
        identity = world.row()[0]['id']
        world.sync.set_share(identity, True)
        world.sync.apply(identity)
        world.hermes.clear()                                  # the user deletes the mirror in Hermes
        self.assertEqual(world.row()[0]['sync'], 'deleted_on_hermes')
        writes = len(world.writes)
        with self.assertRaisesRegex(SyncError, 'deleted'):
            world.sync.apply(identity)
        self.assertEqual(world.sync.apply_all()[0]['result'], 'refused') if world.sync.apply_all() else None
        self.assertEqual(len(world.writes), writes)
        world.sync.set_share(identity, True)                  # explicit re-enable restores
        self.assertEqual(world.sync.apply(identity)['result'], 'added')

    def test_deleting_the_source_never_deletes_the_mirror(self):
        world = World(odysseus=[od_row()])
        identity = world.row()[0]['id']
        world.sync.set_share(identity, True)
        world.sync.apply(identity)
        world.odysseus.clear()
        rows = world.row()
        self.assertEqual((len(rows), rows[0]['source']), (1, 'hermes'))     # mirror still there, untouched
        self.assertEqual(len(world.hermes), 1)
        self.assertFalse(any(name.startswith('delete') or name.startswith('remove') for name in dir(world.sync)))

    def test_changing_a_url_creates_a_new_unshared_identity_and_rewrites_nothing(self):
        world = World(odysseus=[od_row()])
        old = world.row()[0]['id']
        world.sync.set_share(old, True)
        world.sync.apply(old)
        world.odysseus[0].base_url = 'http://localhost:9000/v1'              # conflicting edit on one side
        ids = {r['id']: r for r in world.row()}
        self.assertEqual(len(ids), 2)
        new = [r for i, r in ids.items() if i != old][0]
        self.assertEqual(new['sync'], 'sharing_off')                         # no silent propagation
        self.assertEqual(len(world.hermes), 1)
        self.assertEqual(world.hermes[0]['base_url'], 'http://127.0.0.1:8000/v1')

    def test_no_loops_or_duplicates_across_repeated_runs_in_both_directions(self):
        world = World(odysseus=[od_row()], hermes=[he_item(id='lmstudio', base_url='http://127.0.0.1:1234/v1', display_name='LM Studio', models=[])])
        for row in world.row():
            world.sync.set_share(row['id'], True)
        for _ in range(3):
            world.sync.apply_all()
        self.assertEqual((len(world.odysseus), len(world.hermes)), (2, 2))
        self.assertEqual(sum(1 for w in world.writes), 2)
        self.assertTrue(all(r['sync'] == 'in_sync' for r in world.row()))

    def test_concurrent_apply_writes_exactly_once(self):
        world = World(odysseus=[od_row()])
        identity = world.row()[0]['id']
        world.sync.set_share(identity, True)
        results = []
        threads = [threading.Thread(target=lambda: results.append(world.sync.apply(identity)['result'])) for _ in range(6)]
        [t.start() for t in threads]
        [t.join() for t in threads]
        self.assertEqual(sorted(results), ['added'] + ['already_present'] * 5)
        self.assertEqual(len(world.hermes), 1)

    def test_hermes_offline_or_needing_login_blocks_writes_but_odysseus_discovery_continues(self):
        for state in ('offline', 'requires_authentication'):
            world = World(odysseus=[od_row()], hermes_state=state)
            snapshot = world.sync.snapshot(refresh=True)
            self.assertEqual(snapshot['applications']['hermes'], state)
            self.assertEqual(len(snapshot['providers']), 1)
            identity = snapshot['providers'][0]['id']
            world.sync.set_share(identity, True)
            with self.assertRaises(SyncError):
                world.sync.apply(identity)
            self.assertEqual(world.writes, [])

    def test_offline_local_server_is_retained_and_shown_offline_then_online(self):
        world = World(odysseus=[od_row()], probe=('offline', ()))
        self.assertEqual(world.row()[0]['status']['odysseus'], 'offline')
        self.assertEqual(len(world.odysseus), 1)                              # configuration retained
        world.probe = ('online', ('ornith-gguf', 'extra'))
        row = world.row()[0]
        self.assertEqual(row['status']['odysseus'], 'connected')
        self.assertEqual(row['models'], ['extra', 'ornith-gguf'])
        world.probe = ('requires_authentication', ())
        self.assertEqual(world.row()[0]['status']['odysseus'], 'requires_authentication')

    def test_state_persists_across_service_restarts_and_survives_corruption(self):
        world = World(odysseus=[od_row()])
        identity = world.row()[0]['id']
        world.sync.set_share(identity, True)
        world.sync.apply(identity)
        world.hermes.clear()
        world.row()
        restarted = world.new_sync().snapshot(refresh=True)['providers'][0]
        self.assertEqual((restarted['share_enabled'], restarted['sync']), (True, 'deleted_on_hermes'))
        world.state.clear()
        world.state['share'] = 'garbage'
        self.assertEqual(world.new_sync().snapshot(refresh=True)['providers'][0]['sync'], 'sharing_off')

    def test_test_connection_never_contacts_remote_providers_or_leaks_secrets(self):
        probe = Mock(return_value=('online', ('m',)))
        world = World(odysseus=[od_row(), od_row(id='r', base_url='https://api.example.test/v1', api_key=SECRET)])
        world.sync._probe = probe
        remote = [r for r in world.row() if r['id'].startswith('ep:https')][0]
        probe.reset_mock()
        outcome = world.sync.test(remote['id'])
        self.assertEqual(outcome['state'], 'not_tested')
        self.assertTrue(all(call.args[0].startswith('http://127.0.0.1') for call in probe.call_args_list), 'a remote provider was probed')
        self.assertNotIn(SECRET, json.dumps(outcome))
        local = [r for r in world.row() if r['id'].startswith('ep:http://127')][0]
        self.assertEqual(world.sync.test(local['id'])['state'], 'online')


class WriterTests(unittest.TestCase):
    def test_odysseus_endpoint_creation_is_additive_keyless_and_idempotent(self):
        from sqlalchemy import create_engine
        from sqlalchemy.orm import sessionmaker
        from core.database import ModelEndpoint
        from src.provider_sync_runtime import create_odysseus_endpoint
        engine = create_engine('sqlite://')
        ModelEndpoint.__table__.create(engine)
        factory = sessionmaker(bind=engine)
        existing = factory()
        existing.add(ModelEndpoint(id='mine', name='Mine', base_url='http://localhost:8001/v1', api_key=SECRET, is_enabled=True))
        existing.commit()
        existing.close()
        definition = {'name': 'Local', 'base_url': 'http://127.0.0.1:8000/v1', 'models': ['a'], 'slug': 'ody-1'}
        self.assertEqual(create_odysseus_endpoint(definition, factory, ModelEndpoint), 'created')
        self.assertEqual(create_odysseus_endpoint({**definition, 'base_url': 'http://localhost:8000/v1/'}, factory, ModelEndpoint), 'present')
        check = factory()
        rows = {r.id: r for r in check.query(ModelEndpoint).all()}
        self.assertEqual(len(rows), 2)
        self.assertEqual((rows['mine'].api_key, rows['mine'].name), (SECRET, 'Mine'))     # untouched
        created = [r for i, r in rows.items() if i.startswith('sync-')][0]
        self.assertIsNone(created.api_key)
        self.assertEqual((created.endpoint_kind, created.is_enabled), ('local', True))
        check.close()


HERMES_PYTHON = Path('C:/Users/Protagonist/AppData/Local/hermes/installs/562e58e2ea9aca80/environments/f8e9074e29494d6589e6dc5db08630d3/venv/Scripts/python.exe')


@unittest.skipUnless(HERMES_PYTHON.exists(), 'needs the Hermes interpreter (ruamel.yaml)')
class HermesConfigWriterTests(unittest.TestCase):
    CONFIG = '# keep me\nmodel:\n  default: "claude"\n  provider: "plugin"\n\nagent:\n    items:\n    - a   # trailing\n'

    def run_writer(self, config, request):
        return subprocess.run([str(HERMES_PYTHON), '-I', str(ROOT / 'scripts/hermes_config_append_provider.py'), str(config), str(config.parent / 'bak')],
                              input=json.dumps(request), capture_output=True, text=True)

    def test_appends_only_never_touches_existing_bytes_default_model_or_comments(self):
        with tempfile.TemporaryDirectory(dir=ROOT / '.release-tests') as directory:
            config = Path(directory) / 'config.yaml'
            config.write_text(self.CONFIG, encoding='utf-8', newline='')
            before = config.read_bytes()
            request = {'slug': 'ody-1', 'name': 'Local', 'base_url': 'http://127.0.0.1:8000/v1', 'models': ['a', 'b']}
            self.assertEqual(self.run_writer(config, request).returncode, 0)
            after = config.read_bytes()
            self.assertTrue(after.startswith(before))
            self.assertIn(b'"custom:ody-1"', after[len(before):])
            self.assertEqual(self.run_writer(config, request).returncode, 3)                   # idempotent
            self.assertEqual(self.run_writer(config, {**request, 'slug': 'other', 'base_url': 'http://localhost:8000/v1/'}).returncode, 3)
            self.assertEqual(self.run_writer(config, {**request, 'slug': 'k', 'base_url': 'http://h:1/v1', 'api_key': SECRET}).returncode, 1)
            self.assertEqual(self.run_writer(config, {**request, 'slug': 'u', 'base_url': 'http://u:p@h:1/v1'}).returncode, 1)
            self.assertEqual(self.run_writer(config, {**request, 'slug': 'second', 'base_url': 'http://127.0.0.1:9000/v1', 'models': []}).returncode, 0)
            self.assertTrue(config.read_bytes().startswith(after))
            self.assertNotIn(SECRET.encode(), config.read_bytes())
            self.assertEqual(len(list((Path(directory) / 'bak').iterdir())), 2)

    def test_refuses_when_providers_is_not_the_last_section(self):
        with tempfile.TemporaryDirectory(dir=ROOT / '.release-tests') as directory:
            config = Path(directory) / 'config.yaml'
            text = 'providers:\n  x:\n    base_url: "http://h:1/v1"\nmodel:\n  default: a\n'
            config.write_text(text, encoding='utf-8', newline='')
            result = self.run_writer(config, {'slug': 'n', 'name': 'n', 'base_url': 'http://127.0.0.1:5/v1', 'models': []})
            self.assertEqual(result.returncode, 1)
            self.assertEqual(config.read_text(encoding='utf-8'), text)


if __name__ == '__main__':
    unittest.main()

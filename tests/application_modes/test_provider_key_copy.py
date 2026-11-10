"""Explicit "Copy key to Odysseus": who may do it, what it may copy, and that the key never leaks."""
import json
import logging
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from src.provider_catalog import HERMES_KEY_ENV
from src.provider_sync import SyncError
from src.provider_sync_runtime import make_key_copier, read_env_value, store_odysseus_key
from tests.application_modes.test_provider_sync import World, he_item, od_row

SECRET = 'sk-SEN...6789'
ZEN = 'https://opencode.ai/zen/v1'


def zen(**kw):
    base = dict(id='opencode-zen', display_name='OpenCode Zen', has_key=True, is_self_hosted=False, base_url=None,
                key_source='env_file', models=[{'id': 'big-pickle'}, {'id': 'kimi-k2'}])
    base.update(kw)
    return he_item(**base)


class Selectable:
    def __init__(self, **kw):
        self.__dict__.update(kw)


class FakeDB:
    def __init__(self, rows, fail=False):
        self.rows, self.fail, self.committed, self.rolled_back = rows, fail, False, False

    def query(self, model):
        return Mock(all=lambda: list(self.rows))

    def add(self, row):
        self.rows.append(row)

    def commit(self):
        if self.fail:
            raise RuntimeError('database exploded with parameters ' + SECRET)
        self.committed = True

    def rollback(self):
        self.rolled_back = True

    def close(self):
        pass


class EligibilityTests(unittest.TestCase):
    def status(self, **world):
        return World(**world).row()[0]['key_copy']

    def test_only_an_env_file_key_on_a_listed_provider_is_copyable(self):
        self.assertEqual(self.status(hermes=[zen()]), 'available')
        self.assertEqual(self.status(hermes=[zen(key_source='config_yaml')]), 'unavailable')      # not in .env
        self.assertEqual(self.status(hermes=[zen(is_oauth=True)]), 'unavailable')                  # a login, not a key
        self.assertEqual(self.status(hermes=[zen(id='openai-codex', display_name='Codex', is_oauth=True, key_source='')]), 'unavailable')
        self.assertEqual(self.status(hermes=[zen(id='fireworks', display_name='Fireworks')]), 'unavailable')   # not on the allowlist
        self.assertEqual(self.status(hermes=[zen(id='solstice', display_name='Solstice', base_url='process://x')]), 'unavailable')

    def test_a_provider_that_already_has_a_key_in_odysseus_is_never_overwritable(self):
        keyed = od_row(base_url=ZEN, api_key=SECRET, name='Zen')
        self.assertEqual(self.status(odysseus=[keyed], hermes=[zen()]), 'already')
        keyless = od_row(base_url=ZEN, api_key=None, name='Zen')
        self.assertEqual(self.status(odysseus=[keyless], hermes=[zen()]), 'available')

    def test_the_catalog_never_carries_a_key_or_where_it_is_kept_beyond_a_label(self):
        world = World(hermes=[zen()])
        self.assertNotIn(SECRET, json.dumps(world.row()))


class ServiceTests(unittest.TestCase):
    def setUp(self):
        self.world = World(hermes=[zen()])
        self.copier = Mock(return_value='created')
        self.world.sync._copy_key = self.copier
        self.identity = self.world.row()[0]['id']

    def test_copy_passes_only_a_provider_id_and_a_definition_and_returns_no_secret(self):
        result = self.world.sync.copy_key(self.identity)
        self.assertEqual(result, {'result': 'created', 'id': self.identity})
        provider, definition = self.copier.call_args.args
        self.assertEqual(provider, 'opencode-zen')
        self.assertEqual(set(definition), {'name', 'base_url', 'models'})
        self.assertEqual(definition['base_url'], ZEN)
        self.assertEqual(self.world.invalidated, 1)                      # the model picker is refreshed
        self.assertNotIn(SECRET, json.dumps([result, self.world.state, self.copier.call_args_list]))

    def test_a_deleted_mirror_can_be_restored_by_an_explicit_copy(self):
        self.world.state['tombstones'] = {self.identity: {'by': 'odysseus', 'fingerprint': 'x'}}
        self.world.sync.copy_key(self.identity)
        self.assertNotIn(self.identity, self.world.state.get('tombstones', {}))

    def test_refused_cases_never_reach_the_key_store(self):
        keyed = World(odysseus=[od_row(base_url=ZEN, api_key=SECRET, name='Zen')], hermes=[zen()])
        keyed.sync._copy_key = self.copier
        with self.assertRaisesRegex(SyncError, 'already has a key'):
            keyed.sync.copy_key(keyed.row()[0]['id'])
        oauth = World(hermes=[zen(is_oauth=True)])
        oauth.sync._copy_key = self.copier
        with self.assertRaises(SyncError):
            oauth.sync.copy_key(oauth.row()[0]['id'])
        with self.assertRaises(SyncError):
            self.world.sync.copy_key('ep:https://unknown.example/v1')
        offline = World(hermes=[zen()], hermes_state='offline')
        offline.sync._copy_key = self.copier
        with self.assertRaises(SyncError):
            offline.sync.copy_key(self.identity)
        self.copier.assert_not_called()

    def test_not_configured_is_a_clean_refusal(self):
        self.world.sync._copy_key = None
        with self.assertRaises(SyncError):
            self.world.sync.copy_key(self.identity)


class EnvReaderTests(unittest.TestCase):
    def env(self, text):
        handle = tempfile.NamedTemporaryFile('w', suffix='.env', delete=False, encoding='utf-8')
        handle.write(text)
        handle.close()
        self.addCleanup(lambda: Path(handle.name).unlink(missing_ok=True))
        return handle.name

    def test_dotenv_forms(self):
        path = self.env('# note\nOTHER=1\nexport A_KEY="quoted"\nB_KEY=\'single\'\nC_KEY=plain # trailing\nD_KEY=\n')
        self.assertEqual(read_env_value(path, ('A_KEY',)), 'quoted')
        self.assertEqual(read_env_value(path, ('B_KEY',)), 'single')
        self.assertEqual(read_env_value(path, ('C_KEY',)), 'plain')
        self.assertIsNone(read_env_value(path, ('D_KEY',)))
        self.assertIsNone(read_env_value(path, ('MISSING',)))
        self.assertIsNone(read_env_value(path + '.nope', ('A_KEY',)))

    def test_canonical_name_wins_and_the_shared_alias_is_the_fallback(self):
        both = self.env('OPENCODE_API_KEY=alias-value\nOPENCODE_ZEN_API_KEY=canonical-value\n')
        self.assertEqual(read_env_value(both, HERMES_KEY_ENV['opencode-zen']), 'canonical-value')
        alias_only = self.env('OPENCODE_API_KEY=alias-value\nOPENCODE_ZEN_API_KEY=\n')
        self.assertEqual(read_env_value(alias_only, HERMES_KEY_ENV['opencode-zen']), 'alias-value')

    def test_an_oversized_file_is_ignored(self):
        self.assertIsNone(read_env_value(self.env('A_KEY=x\n' + '#' * (1 << 20 + 1)), ('A_KEY',)))


class StoreTests(unittest.TestCase):
    definition = {'name': 'OpenCode Zen', 'base_url': ZEN, 'models': ['big-pickle']}

    def setUp(self):
        allow = patch('src.url_safety.check_outbound_url', return_value=(True, ''))
        allow.start()
        self.addCleanup(allow.stop)

    def copier(self, text, db):
        handle = tempfile.NamedTemporaryFile('w', suffix='.env', delete=False, encoding='utf-8')
        handle.write(text)
        handle.close()
        self.addCleanup(lambda: Path(handle.name).unlink(missing_ok=True))
        return make_key_copier(handle.name, lambda: db, Selectable)

    def test_creates_the_endpoint_with_the_key_and_logs_no_secret(self):
        db = FakeDB([])
        with self.assertLogs('src.provider_sync_runtime', level='INFO') as logs:
            outcome = self.copier(f'OPENCODE_ZEN_API_KEY={SECRET}\n', db)('opencode-zen', self.definition)
        self.assertEqual(outcome, 'created')
        row = db.rows[0]
        self.assertEqual((row.api_key, row.base_url, row.endpoint_kind, row.is_enabled), (SECRET, ZEN, 'auto', True))
        self.assertEqual(json.loads(row.cached_models), ['big-pickle'])
        self.assertNotIn(SECRET, '\n'.join(logs.output))
        self.assertIn('opencode-zen', '\n'.join(logs.output))

    def test_fills_an_empty_key_but_never_replaces_an_existing_one(self):
        empty = Selectable(base_url=ZEN, api_key=None)
        self.assertEqual(self.copier(f'OPENCODE_ZEN_API_KEY={SECRET}\n', FakeDB([empty]))('opencode-zen', self.definition), 'key_set')
        self.assertEqual(empty.api_key, SECRET)
        keyed = Selectable(base_url=ZEN, api_key='the-users-own-key')
        with self.assertRaisesRegex(SyncError, 'already has a key'):
            self.copier(f'OPENCODE_ZEN_API_KEY={SECRET}\n', FakeDB([keyed]))('opencode-zen', self.definition)
        self.assertEqual(keyed.api_key, 'the-users-own-key')

    def test_unlisted_providers_and_unusable_values_are_refused_before_anything_is_stored(self):
        db = FakeDB([])
        with self.assertRaises(SyncError):
            self.copier(f'FIREWORKS_API_KEY={SECRET}\n', db)('fireworks', self.definition)
        for bad in ('', 'short', 'has space inside-the-key', 'x' * 5000, 'tab\there1234'):
            with self.assertRaises(SyncError, msg=bad[:12]):
                self.copier(f'OPENCODE_ZEN_API_KEY={bad}\n', db)('opencode-zen', self.definition)
        with self.assertRaises(SyncError):
            self.copier('NOTHING=1\n', db)('opencode-zen', self.definition)
        self.assertEqual(db.rows, [])

    def test_a_database_failure_is_reported_without_the_key_or_its_traceback(self):
        db = FakeDB([], fail=True)
        with self.assertRaises(SyncError) as raised:
            self.copier(f'OPENCODE_ZEN_API_KEY={SECRET}\n', db)('opencode-zen', self.definition)
        self.assertNotIn(SECRET, str(raised.exception))
        self.assertTrue(raised.exception.__suppress_context__)
        self.assertTrue(db.rolled_back)

    def test_a_url_odysseus_would_reject_is_refused(self):
        with patch('src.url_safety.check_outbound_url', return_value=(False, 'blocked')):
            with self.assertRaisesRegex(SyncError, 'rejected'):
                self.copier(f'OPENCODE_ZEN_API_KEY={SECRET}\n', FakeDB([]))('opencode-zen', self.definition)


class RealColumnTests(unittest.TestCase):
    """Through Odysseus's own ModelEndpoint model and EncryptedText column on a throw-away SQLite file."""

    def test_the_key_is_encrypted_at_rest_and_round_trips(self):
        from sqlalchemy import create_engine
        from sqlalchemy.orm import sessionmaker
        import src.secret_storage as storage
        from core.database import ModelEndpoint
        with tempfile.TemporaryDirectory() as tmp:
            self.addCleanup(setattr, storage, '_fernet', None)
            with patch.object(storage, '_KEY_PATH', Path(tmp) / 'app.key'), patch.object(storage, '_fernet', None), \
                    patch('src.url_safety.check_outbound_url', return_value=(True, '')):
                engine = create_engine('sqlite:///' + str(Path(tmp) / 'endpoints.db'))
                ModelEndpoint.__table__.create(engine)
                factory = sessionmaker(bind=engine)
                self.assertEqual(store_odysseus_key({'name': 'Zen', 'base_url': ZEN, 'models': ['m']}, SECRET, factory, ModelEndpoint), 'created')
                connection = sqlite3.connect(Path(tmp) / 'endpoints.db')
                try:
                    raw = connection.execute('select api_key from model_endpoints').fetchone()[0]
                finally:
                    connection.close()
                self.assertTrue(raw.startswith('enc:'))
                self.assertNotIn(SECRET, raw)
                session = factory()
                self.assertEqual(session.query(ModelEndpoint).one().api_key, SECRET)
                session.close()
                engine.dispose()
                for path in Path(tmp).glob('endpoints.db*'):
                    self.assertNotIn(SECRET.encode(), path.read_bytes())


if __name__ == '__main__':
    unittest.main()

import json
import sqlite3
import sys
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from src import hermes_backup as backup

MARKERS = [b'SECRET-HERMES-KEY', b'SECRET-OAUTH', b'SECRET-SIGNING', b'SECRET-IMAP', b'SECRET-TAVILY', b'SECRET-APPKEY', b'SECRET-ENVTOKEN', b'SECRET-SESSION']


def make_tree(base):
    ody, hermes, data = base / 'ody', base / 'hermes', base / 'ody' / 'data'
    data.mkdir(parents=True)
    hermes.mkdir()
    (ody / '.env').write_text('APP_PORT=7000\nADMIN_PASSWORD=SECRET-ENVTOKEN\nODYSSEUS_HERMES_BACKEND=webapp\n', encoding='utf-8')
    (hermes / '.env').write_text('# note\nTERMINAL_TIMEOUT=30\nOPENCODE_ZEN_API_KEY=SECRET-HERMES-KEY\n', encoding='utf-8')
    (hermes / '.env.bak-1').write_text('OPENCODE_ZEN_API_KEY=SECRET-HERMES-KEY\n', encoding='utf-8')
    (hermes / 'auth.json').write_text(json.dumps({'providers': {'x': {'access_token': 'SECRET-OAUTH'}}}), encoding='utf-8')
    (hermes / 'config.yaml').write_text('model:\n  default: m\n  api_key: ${MY_KEY}\nterminal:\n  backend: local\n', encoding='utf-8')
    (hermes / 'notes.md').write_text('hello', encoding='utf-8')
    (hermes / 'webui').mkdir()
    (hermes / 'webui' / '.signing_key').write_text('SECRET-SIGNING', encoding='utf-8')
    (data / '.app_key').write_text('SECRET-APPKEY', encoding='utf-8')
    (data / 'sessions.json').write_text(json.dumps({'tok': 'SECRET-SESSION'}), encoding='utf-8')
    (data / 'settings.json').write_text(json.dumps({'theme': 'dark', 'tavily_api_key': 'SECRET-TAVILY', 'nested': {'n': 1}}), encoding='utf-8')
    with closing(sqlite3.connect(data / 'app.db')) as db:
        db.execute('create table email_accounts (id integer primary key, address text, imap_password text, smtp_password text not null, oauth_refresh_token text)')
        db.execute("insert into email_accounts values (1, 'me@example.invalid', 'SECRET-IMAP', 'SECRET-IMAP', null)")
        db.execute('create table sessions (id integer primary key, title text, total_input_tokens integer, max_tokens_override integer)')
        db.execute("insert into sessions values (1, 'a chat', 123, 456)")
        db.commit()
    return ody, hermes, data


def everything(folder):
    blob = b''
    for path in Path(folder).rglob('*'):
        if path.is_file():
            blob += path.read_bytes()
    return blob


class ExcludeSecrets(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.ody, self.hermes, self.data = make_tree(self.base)
        self.backups = self.base / 'backups'

    def snap(self, **options):
        return backup.create_backup(self.ody, self.hermes, self.backups, data_dir=self.data, **options)

    def test_default_backup_is_unchanged_and_still_contains_the_secrets(self):
        blob = everything(self.snap())
        for marker in MARKERS:
            self.assertIn(marker, blob)

    def test_no_secret_value_survives_anywhere_in_the_snapshot_and_it_still_verifies(self):
        before = {p: p.read_bytes() for p in self.base.rglob('*') if p.is_file() and 'backups' not in p.parts}
        snapshot = self.snap(exclude_secrets=True)
        blob = everything(snapshot)
        for marker in MARKERS:
            self.assertNotIn(marker, blob, marker)
        self.assertTrue(backup.verify_backup(snapshot)['verified'])
        self.assertEqual(before, {p: p.read_bytes() for p in self.base.rglob('*') if p.is_file() and 'backups' not in p.parts}, 'the live files must not change')

    def test_settings_survive_and_the_manifest_says_what_was_removed(self):
        snapshot = self.snap(exclude_secrets=True)
        env = (snapshot / 'odysseus' / '.env').read_text(encoding='utf-8')
        self.assertIn('APP_PORT=7000', env)
        self.assertIn('ODYSSEUS_HERMES_BACKEND=webapp', env)
        self.assertNotIn('ADMIN_PASSWORD', env)
        self.assertIn('TERMINAL_TIMEOUT=30', (snapshot / 'hermes' / '.env').read_text(encoding='utf-8'))
        settings = json.loads((snapshot / 'odysseus' / 'data' / 'settings.json').read_text(encoding='utf-8'))
        self.assertEqual((settings['theme'], settings['tavily_api_key'], settings['nested']), ('dark', '', {'n': 1}))
        self.assertTrue((snapshot / 'hermes' / 'notes.md').is_file())
        with closing(sqlite3.connect(snapshot / 'odysseus' / 'data' / 'app.db')) as db:
            self.assertEqual(db.execute('select address, imap_password, smtp_password from email_accounts').fetchone(), ('me@example.invalid', '', ''))
            self.assertEqual(db.execute('select title, total_input_tokens, max_tokens_override from sessions').fetchone(), ('a chat', 123, 456))
        manifest = json.loads((snapshot / 'manifest.json').read_text(encoding='utf-8'))
        self.assertEqual(manifest['secrets']['mode'], 'excluded')
        self.assertTrue(any(path.endswith('auth.json') for path in manifest['secrets']['files_not_copied']))
        self.assertFalse(any(path.endswith(('auth.json', '.signing_key', '.app_key')) for path in (e['source'] for e in manifest['entries'])))
        self.assertIn('SECRETS EXCLUDED', manifest['scope'])
        for marker in MARKERS:
            self.assertNotIn(marker.decode(), json.dumps(manifest))

    def test_a_large_json_file_is_left_out_and_reported_not_copied_unchecked(self):
        (self.hermes / 'models_cache.json').write_text(json.dumps({'k': 'x' * (backup._JSON_SCRUB_LIMIT + 10), 'api_key': 'SECRET-HERMES-KEY'}), encoding='utf-8')
        snapshot = self.snap(exclude_secrets=True)
        self.assertFalse((snapshot / 'hermes' / 'models_cache.json').exists())
        self.assertEqual(json.loads((snapshot / 'manifest.json').read_text(encoding='utf-8'))['secrets']['too_large_to_scan_not_copied'], ['hermes/models_cache.json'])
        self.assertTrue(backup.verify_backup(snapshot)['verified'])

    def test_an_inline_secret_in_a_yaml_config_refuses_the_backup(self):
        (self.hermes / 'config.yaml').write_text('model:\n  api_key: sk-abcdefghijklmnop\n', encoding='utf-8')
        with self.assertRaises(ValueError):
            self.snap(exclude_secrets=True)
        self.snap()                                                                         # the normal backup is still allowed

    def test_restoring_a_secret_free_snapshot_never_erases_live_secrets(self):
        snapshot = self.snap(exclude_secrets=True)
        live = {p: p.read_bytes() for p in (self.ody / '.env', self.hermes / '.env', self.data / 'app.db', self.data / 'settings.json')}
        plan = backup.restore_backup(snapshot, self.ody, self.hermes, data_dir=self.data, apply=True, safety_backup_root=self.backups)
        self.assertTrue(plan['secrets_excluded'] and plan['kept_live_secrets'])
        for path, content in live.items():
            self.assertEqual(path.read_bytes(), content, str(path))
        backup.restore_backup(snapshot, self.ody, self.hermes, data_dir=self.data, apply=True, safety_backup_root=self.backups, overwrite_redacted=True)
        self.assertNotIn(b'SECRET-HERMES-KEY', (self.hermes / '.env').read_bytes())            # only when explicitly asked

    def test_restore_into_an_empty_place_recreates_the_files_without_secrets(self):
        snapshot = self.snap(exclude_secrets=True)
        fresh = self.base / 'fresh'
        (fresh / 'ody' / 'data').mkdir(parents=True)
        (fresh / 'hermes').mkdir()
        backup.restore_backup(snapshot, fresh / 'ody', fresh / 'hermes', data_dir=fresh / 'ody' / 'data', apply=True, safety_backup_root=False)
        self.assertEqual((fresh / 'hermes' / 'notes.md').read_text(encoding='utf-8'), 'hello')
        self.assertFalse((fresh / 'hermes' / 'auth.json').exists())
        for marker in MARKERS:
            self.assertNotIn(marker, everything(fresh))


if __name__ == '__main__':
    unittest.main()

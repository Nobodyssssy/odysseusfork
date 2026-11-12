import json
import os
import sqlite3
import sys
import tempfile
import unittest
import zipfile
from contextlib import closing
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from src import hermes_migration as m

try:
    import ruamel.yaml  # noqa: F401
    HAVE_YAML = True
except ImportError:
    HAVE_YAML = False


def make_home(base, sessions=2):
    home = Path(base) / 'src-home'
    (home / 'memories').mkdir(parents=True)
    (home / 'skills' / 'mine').mkdir(parents=True)
    (home / 'plugins' / 'p1').mkdir(parents=True)
    (home / 'cache').mkdir()
    (home / 'cache' / 'big.bin').write_bytes(b'0' * 100)
    (home / 'memories' / 'MEMORY.md').write_text('remember', encoding='utf-8')
    (home / 'skills' / 'mine' / 'SKILL.md').write_text('skill', encoding='utf-8')
    (home / 'skills' / 'mine' / 'leaked.pem').write_text('PRIVATE', encoding='utf-8')
    (home / 'plugins' / 'p1' / 'plugin.yaml').write_text('name: p1', encoding='utf-8')
    (home / 'plugins' / 'p1' / 'cache.sqlite').write_text('x', encoding='utf-8')
    (home / 'auth.json').write_text(json.dumps({'providers': {'openai': {'access_token': 'SECRET-OAUTH'}}, 'credential_pool': {'openai': [1, 2]}}), encoding='utf-8')
    (home / '.env').write_text('OPENAI_API_KEY=SECRET-KEY\nTERMINAL_TIMEOUT=30\nBOT_TOKEN=SECRET-BOT\n', encoding='utf-8')
    (home / 'config.yaml').write_text('_config_version: 53\nmodel:\n  default: m\n  api_key: SECRET-INLINE\napprovals:\n  mode: off\ncommand_allowlist:\n  - rm\nwebapp:\n  port: 1\n', encoding='utf-8')
    (home / 'vault').mkdir()
    (home / 'vault' / 'webui-trusted-workspaces.json').write_text(json.dumps({'blocked': [{'path': 'D:/private'}]}), encoding='utf-8')
    with closing(sqlite3.connect(home / 'state.db')) as db:
        db.execute('create table sessions (id text)')
        db.execute('create table messages (id integer, session_id text)')
        for index in range(sessions):
            db.execute('insert into sessions values (?)', (f's{index}',))
            db.execute('insert into messages values (?, ?)', (index, f's{index}'))
        db.commit()
    return home


class Classification(unittest.TestCase):
    def test_env_names_are_split_without_reading_values(self):
        settings, credentials = m.classify_env(['OPENAI_API_KEY', 'TERMINAL_TIMEOUT', 'TELEGRAM_BOT_TOKEN', 'WEB_TOOLS_DEBUG', 'DB_PASSWORD'])
        self.assertEqual(settings, ['TERMINAL_TIMEOUT', 'WEB_TOOLS_DEBUG'])
        self.assertEqual(credentials, ['DB_PASSWORD', 'OPENAI_API_KEY', 'TELEGRAM_BOT_TOKEN'])

    def test_top_level_policy(self):
        self.assertEqual(m.classify('auth.json', False)[0], 'reauth')
        self.assertEqual(m.classify('state.db', False)[0], 'copy-db')
        self.assertEqual(m.classify('webui', True)[0], 'stay')
        self.assertEqual(m.classify('profiles', True)[0], 'manual')
        self.assertEqual(m.classify('hermes-agent', True)[0], 'skip-code')
        self.assertEqual(m.classify('gateway.pid', False)[0], 'skip-runtime')
        self.assertEqual(m.classify('state.db-wal', False)[0], 'skip-unknown')                    # unknown things never move
        self.assertEqual(m.classify('mystery', True)[0], 'skip-unknown')
        self.assertEqual(m.classify('config.yaml.bak-1', False)[0], 'skip-cache')

    def test_approval_decisions_are_never_imported(self):
        for key in ('approvals', 'command_allowlist', 'webapp', 'desktop'):
            self.assertIn(key, m.NEVER_IMPORT)
            self.assertNotIn(key, m.IMPORT_SECTIONS)


@unittest.skipUnless(HAVE_YAML, 'needs the Hermes interpreter (ruamel.yaml)')
class Bundle(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = make_home(self.tmp.name)
        self.out = Path(self.tmp.name) / 'bundle'

    def test_bundle_carries_data_but_no_credentials(self):
        manifest = m.build_bundle(self.home, self.out, sys.executable)
        names = zipfile.ZipFile(self.out / 'data.zip').namelist()
        self.assertIn('memories/MEMORY.md', names)
        self.assertIn('skills/mine/SKILL.md', names)
        self.assertIn('plugins/p1/plugin.yaml', names)
        self.assertIn('state.db', names)
        for forbidden in ('auth.json', '.env', 'skills/mine/leaked.pem', 'plugins/p1/cache.sqlite', 'cache/big.bin'):
            self.assertNotIn(forbidden, names)
        everything = b''.join((self.out / f).read_bytes() for f in os.listdir(self.out) if f != 'data.zip') + b''.join(zipfile.ZipFile(self.out / 'data.zip').read(n) for n in names)
        for secret in (b'SECRET-OAUTH', b'SECRET-KEY', b'SECRET-BOT', b'SECRET-INLINE', b'PRIVATE'):
            self.assertNotIn(secret, everything)
        self.assertEqual(manifest['env']['credentials_not_carried'], ['BOT_TOKEN', 'OPENAI_API_KEY'])
        self.assertEqual(manifest['env']['settings_carried'], ['TERMINAL_TIMEOUT'])
        self.assertEqual(manifest['databases']['state.db'], {'sessions': 2, 'messages': 2})
        self.assertIn('skills/mine/leaked.pem', manifest['left_out']['credential_files'])
        self.assertIn('plugins/p1/cache.sqlite', manifest['left_out']['databases'])

    def test_config_drops_approvals_and_inline_secrets(self):
        m.build_bundle(self.home, self.out, sys.executable)
        text = (self.out / 'config.filtered.yaml').read_text(encoding='utf-8')
        self.assertIn('default: m', text)
        for gone in ('approvals', 'command_allowlist', 'webapp', 'api_key', 'SECRET-INLINE'):
            self.assertNotIn(gone, text)

    def test_one_named_credential_can_be_carried_on_purpose(self):
        m.build_bundle(self.home, self.out, sys.executable, carry_credentials=['OPENAI_API_KEY'])
        env = (self.out / 'env.settings').read_text(encoding='utf-8')
        self.assertIn('OPENAI_API_KEY=SECRET-KEY', env)
        self.assertNotIn('BOT_TOKEN', env)
        with self.assertRaises(m.MigrationError):
            m.build_bundle(self.home, Path(self.tmp.name) / 'b2', sys.executable, carry_credentials=['NOT_THERE'])

    def test_blocked_folders_become_the_neutral_list(self):
        m.build_bundle(self.home, self.out, sys.executable)
        self.assertEqual(json.loads((self.out / 'blocked.json').read_text(encoding='utf-8'))['blocked'], [{'path': 'D:/private'}])

    def test_source_is_untouched_and_existing_bundle_not_reused(self):
        before = {p.relative_to(self.home).as_posix(): p.read_bytes() for p in self.home.rglob('*') if p.is_file()}
        m.build_bundle(self.home, self.out, sys.executable)
        self.assertEqual(before, {p.relative_to(self.home).as_posix(): p.read_bytes() for p in self.home.rglob('*') if p.is_file()})
        with self.assertRaises(m.MigrationError):
            m.build_bundle(self.home, self.out, sys.executable)

    def test_modified_bundle_is_refused(self):
        m.build_bundle(self.home, self.out, sys.executable)
        with open(self.out / 'data.zip', 'ab') as handle:
            handle.write(b'x')
        with self.assertRaises(m.MigrationError):
            m.read_manifest(self.out)

    def test_preflight_refuses_populated_or_same_or_running_targets(self):
        m.build_bundle(self.home, self.out, sys.executable)
        target = Path(self.tmp.name) / 'managed'
        target.mkdir()
        self.assertTrue(m.preflight(self.out, target))                                          # no config.yaml
        (target / 'config.yaml').write_text('x: 1\n', encoding='utf-8')
        self.assertEqual(m.preflight(self.out, target), [])
        self.assertTrue(m.preflight(self.out, self.home))                                       # the source itself
        with closing(sqlite3.connect(target / 'state.db')) as db:
            db.execute('create table sessions (id text)')
            db.execute('create table messages (id integer)')
            db.execute("insert into sessions values ('a')")
            db.commit()
        self.assertTrue(any('already holds' in p for p in m.preflight(self.out, target)))

    def test_rollback_restores_and_keeps_the_migrated_copy(self):
        target = Path(self.tmp.name) / 'managed'
        target.mkdir()
        (target / 'config.yaml').write_text('original: true\n', encoding='utf-8')
        safety = Path(self.tmp.name) / 'safety.zip'
        with zipfile.ZipFile(safety, 'w') as zf:
            zf.write(target / 'config.yaml', 'config.yaml')
        (target / 'config.yaml').write_text('migrated: true\n', encoding='utf-8')
        (target / 'state.db').write_text('data', encoding='utf-8')
        aside = Path(m.rollback(target, safety))
        self.assertEqual((target / 'config.yaml').read_text(encoding='utf-8'), 'original: true\n')
        self.assertFalse((target / 'state.db').exists())
        self.assertTrue((aside / 'state.db').exists())


if __name__ == '__main__':
    unittest.main()

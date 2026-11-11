"""Full backup and offline restore. Everything runs against disposable folders; no real state is touched."""
from contextlib import closing
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]


class Fixture:
    def __init__(self, test):
        parent = ROOT / '.release-tests'
        parent.mkdir(exist_ok=True)
        self.directory = tempfile.TemporaryDirectory(dir=parent)
        test.addCleanup(self.directory.cleanup)
        base = Path(self.directory.name)
        self.ody, self.hermes, self.component = base / 'ody', base / 'hermes', base / 'component'
        self.startup, self.backups = base / 'startup', base / 'backups'
        (self.ody / 'data/chroma').mkdir(parents=True)
        (self.ody / 'data/fastembed_cache').mkdir()
        (self.hermes / 'plugins/claude').mkdir(parents=True)
        (self.hermes / 'hermes-agent').mkdir()
        (self.hermes / 'sessions').mkdir()
        (self.component / 'managed').mkdir(parents=True)
        self.startup.mkdir()
        (self.ody / 'data/settings.json').write_text('{"theme":"dark"}')
        (self.ody / 'data/chroma/index.bin').write_bytes(b'vectors')
        (self.ody / 'data/fastembed_cache/model.onnx').write_bytes(b'weights')
        (self.ody / '.env').write_text('KEY=1')
        (self.hermes / 'config.yaml').write_text('model: x')
        (self.hermes / 'plugins/claude/directsdk.py').write_text('PLUGIN')
        (self.hermes / 'hermes-agent/skip.py').write_text('runtime')
        (self.hermes / 'sessions/a.json').write_text('{"chat":1}')
        (self.component / 'managed/component.json').write_text('{"version":1,"enabled":true,"home":"x"}')
        (self.startup / 'start-hidden.vbs').write_text('WScript.Quit')
        with closing(sqlite3.connect(self.ody / 'data/app.db')) as db:
            db.execute('pragma journal_mode=wal')
            db.execute('create table messages (text text)')
            db.execute("insert into messages values ('first conversation')")
            db.commit()
        self.env = {'HERMES_WRITE_SAFE_ROOT': 'E:\\Project', 'UNRELATED': 'no'}

    def roots(self):
        return {'odysseus': self.ody, 'data': self.ody / 'data', 'hermes': self.hermes, 'component': self.component,
                'startup': self.startup, 'backup_dir': self.backups}


class FullBackupTests(unittest.TestCase):
    def setUp(self):
        from src import hermes_backup
        self.backup = hermes_backup
        self.fx = Fixture(self)

    def snapshot(self):
        destination, report = self.backup.create_full_backup(environment=self.fx.env, roots=self.fx.roots())
        return destination, report

    def restore(self, destination, **options):
        fx = self.fx
        return self.backup.restore_backup(destination, fx.ody, fx.hermes, fx.component, fx.startup, **options)

    def test_snapshot_covers_both_apps_and_leaves_out_runtime_and_weights(self):
        destination, report = self.snapshot()
        self.assertTrue(report['verified'])
        manifest = json.loads((destination / 'manifest.json').read_text())
        relative = {item['relative_path'] for item in manifest['entries']}
        for expected in ('odysseus/data/app.db', 'odysseus/data/settings.json', 'odysseus/data/chroma/index.bin', 'odysseus/.env',
                         'hermes/config.yaml', 'hermes/plugins/claude/directsdk.py', 'hermes/sessions/a.json',
                         'component/managed/component.json', 'startup/start-hidden.vbs'):
            self.assertIn(expected, relative)
        self.assertFalse(any('skip.py' in path or 'model.onnx' in path for path in relative))
        self.assertEqual(manifest['environment'], {'HERMES_WRITE_SAFE_ROOT': 'E:\\Project'})
        self.assertEqual(manifest['version'], 2)

    def test_backup_inside_a_state_tree_is_refused(self):
        with self.assertRaises(ValueError):
            self.backup.create_backup(self.fx.ody, self.fx.hermes, self.fx.hermes / 'inside', data_dir=self.fx.ody / 'data')

    def test_dry_run_changes_nothing(self):
        destination, _ = self.snapshot()
        (self.fx.hermes / 'config.yaml').write_text('model: changed')
        summary = self.restore(destination, data_dir=self.fx.ody / 'data')
        self.assertFalse(summary['applied'])
        self.assertGreaterEqual(summary['overwrite'], 1)
        self.assertEqual((self.fx.hermes / 'config.yaml').read_text(), 'model: changed')

    def test_restore_brings_back_files_and_databases_and_deletes_nothing(self):
        destination, _ = self.snapshot()
        fx = self.fx
        (fx.hermes / 'config.yaml').write_text('model: broken')
        (fx.hermes / 'sessions/a.json').unlink()
        (fx.ody / 'data/settings.json').write_text('{"theme":"wrecked"}')
        (fx.ody / 'data/newer.txt').write_text('made after the snapshot')
        with closing(sqlite3.connect(fx.ody / 'data/app.db')) as db:
            db.execute("insert into messages values ('written after the snapshot')")
            db.commit()
        (fx.ody / 'data/app.db-wal').write_bytes(b'stale journal')
        summary = self.restore(destination, data_dir=fx.ody / 'data', apply=True, safety_backup_root=fx.backups)
        self.assertTrue(summary['applied'])
        self.assertEqual(summary['failed'], [])
        self.assertEqual((fx.hermes / 'config.yaml').read_text(), 'model: x')
        self.assertEqual((fx.hermes / 'sessions/a.json').read_text(), '{"chat":1}')
        self.assertEqual((fx.ody / 'data/settings.json').read_text(), '{"theme":"dark"}')
        self.assertEqual((fx.ody / 'data/newer.txt').read_text(), 'made after the snapshot')
        with closing(sqlite3.connect(fx.ody / 'data/app.db')) as db:
            self.assertEqual(db.execute('select text from messages').fetchall(), [('first conversation',)])
            self.assertEqual(db.execute('pragma integrity_check').fetchone()[0], 'ok')
        self.assertFalse((fx.ody / 'data/app.db-wal').exists())
        before = [row for row in self.backup.list_backups(fx.backups) if row['kind'] == 'prerestore']
        self.assertEqual(len(before), 1)
        self.assertEqual(Path(summary['safety_backup']).name, before[0]['name'])
        self.assertTrue(self.backup.verify_backup(summary['safety_backup'])['verified'])

    def test_overwriting_requires_a_safety_backup_location_and_a_quiet_system(self):
        destination, _ = self.snapshot()
        (self.fx.hermes / 'config.yaml').write_text('model: changed')
        with self.assertRaises(ValueError):
            self.restore(destination, data_dir=self.fx.ody / 'data', apply=True)
        with self.assertRaises(self.backup.RestoreBlocked):
            self.restore(destination, data_dir=self.fx.ody / 'data', apply=True, safety_backup_root=self.fx.backups, busy=['port 8787'])
        self.assertEqual((self.fx.hermes / 'config.yaml').read_text(), 'model: changed')

    def test_restore_into_an_empty_reinstall_recreates_everything(self):
        destination, _ = self.snapshot()
        fresh = Path(self.fx.directory.name) / 'fresh'
        for name in ('ody', 'hermes', 'component', 'startup'):
            (fresh / name).mkdir(parents=True)
        summary = self.backup.restore_backup(destination, fresh / 'ody', fresh / 'hermes', fresh / 'component', fresh / 'startup',
                                             data_dir=fresh / 'ody/data', apply=True, safety_backup_root=False)
        self.assertEqual(summary['failed'], [])
        self.assertEqual(summary['overwrite'], 0)
        self.assertEqual((fresh / 'hermes/plugins/claude/directsdk.py').read_text(), 'PLUGIN')
        self.assertTrue((fresh / 'component/managed/component.json').is_file())
        self.assertTrue((fresh / 'startup/start-hidden.vbs').is_file())
        with closing(sqlite3.connect(fresh / 'ody/data/app.db')) as db:
            self.assertEqual(db.execute('select text from messages').fetchone()[0], 'first conversation')

    def test_missing_destination_is_skipped_not_guessed(self):
        destination, _ = self.snapshot()
        summary = self.backup.restore_backup(destination, self.fx.ody, None, None, None, data_dir=self.fx.ody / 'data')
        self.assertTrue(summary['skipped'])
        self.assertTrue(all('hermes' in item['reason'] or 'component' in item['reason'] or 'startup' in item['reason'] for item in summary['skipped']))

    def test_tampered_snapshot_is_rejected_before_anything_is_written(self):
        destination, _ = self.snapshot()
        (destination / 'hermes/config.yaml').write_text('model: evil')
        (self.fx.hermes / 'config.yaml').write_text('model: mine')
        with self.assertRaises(ValueError):
            self.restore(destination, data_dir=self.fx.ody / 'data', apply=True, safety_backup_root=self.fx.backups)
        self.assertEqual((self.fx.hermes / 'config.yaml').read_text(), 'model: mine')

    def test_manifest_paths_cannot_escape_the_destination(self):
        destination, _ = self.snapshot()
        manifest_path = destination / 'manifest.json'
        manifest = json.loads(manifest_path.read_text())
        evil = destination / 'hermes/evil.txt'
        evil.write_text('x')
        manifest['entries'].append({'source': 'x', 'relative_path': 'hermes/../../escaped.txt', 'bytes': 1,
                                    'sha256': self.backup._digest(evil), 'method': 'stable-copy'})
        (destination / 'escaped.txt').write_text('x')
        manifest_path.write_text(json.dumps(manifest))
        with self.assertRaises(ValueError):
            self.restore(destination, data_dir=self.fx.ody / 'data')

    def test_snapshot_names_are_validated(self):
        destination, _ = self.snapshot()
        self.assertEqual(self.backup.snapshot_path(self.fx.backups, destination.name), destination.resolve())
        for bad in ('..', '../x', destination.name + '/..', 'snapshot-1', None, 7):
            with self.assertRaises(ValueError):
                self.backup.snapshot_path(self.fx.backups, bad)

    def test_tasks_are_saved_and_only_registered_on_request(self):
        fx = self.fx
        destination, _ = self.backup._build_backup(fx.ody, fx.hermes, fx.backups, task_xmls={'Odysseus AutoStart': '<?xml version="1.0"?><Task/>'},
                                                    component_root=fx.component, data_dir=fx.ody / 'data')
        manifest = json.loads((destination / 'manifest.json').read_text())
        self.assertEqual(list(manifest['tasks']), ['Odysseus AutoStart'])
        calls = []
        self.restore(destination, data_dir=fx.ody / 'data', apply=True, safety_backup_root=False, task_importer=lambda n, x: calls.append(n))
        self.assertEqual(calls, [])
        summary = self.restore(destination, data_dir=fx.ody / 'data', apply=True, safety_backup_root=False, restore_tasks=True,
                               task_importer=lambda n, x: calls.append((n, x)))
        self.assertEqual(calls, [('Odysseus AutoStart', '<?xml version="1.0"?><Task/>')])
        self.assertEqual(summary['tasks_restored'], ['Odysseus AutoStart'])
        for bad in ('x"; calc; "', '..\\..\\evil', ''):
            with self.assertRaises(ValueError):
                self.backup._build_backup(fx.ody, fx.hermes, fx.backups, task_xmls={bad: '<Task/>'}, data_dir=fx.ody / 'data')

    def test_running_gateway_and_busy_port_are_reported(self):
        import socket
        with socket.socket() as sock:
            sock.bind(('127.0.0.1', 0))
            sock.listen()
            port = sock.getsockname()[1]
            self.assertEqual(self.backup.running_services(None, [port]), [f'port {port}'])
        import os
        (self.fx.hermes / 'gateway.pid').write_text(json.dumps({'pid': os.getpid()}))
        self.assertTrue(any('gateway' in name for name in self.backup.running_services(self.fx.hermes)))
        (self.fx.hermes / 'gateway.pid').write_text(json.dumps({'pid': 2 ** 22 + 12345}))
        self.assertEqual(self.backup.running_services(self.fx.hermes), [])


if __name__ == '__main__':
    unittest.main()

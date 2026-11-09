"""Backup verification uses live disposable WAL SQLite files, not real state."""
from contextlib import closing
import importlib.util
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]


class BackupTests(unittest.TestCase):
    def test_online_sqlite_and_settings_backup_are_verified_and_outside_checkout(self):
        self.assertIsNotNone(importlib.util.find_spec('src.hermes_backup'), 'verified external backups are missing')
        from src.hermes_backup import create_backup, verify_backup
        parent = ROOT / '.release-tests'
        parent.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=parent) as directory:
            base = Path(directory)
            ody, hermes = base / 'ody', base / 'hermes'
            (ody / 'data').mkdir(parents=True)
            hermes.mkdir()
            (ody / 'data/settings.json').write_text('{"preserve":true}')
            (hermes / 'memory.txt').write_text('remember this')
            with closing(sqlite3.connect(ody / 'data/app.db')) as live:
                live.execute('pragma journal_mode=wal')
                live.execute('create table messages (text text)')
                live.execute('insert into messages values (?)', ('preserved conversation',))
                live.commit()
                backup = create_backup(ody, hermes, base / 'backups')
            report = verify_backup(backup)
            self.assertTrue(report['verified'])
            self.assertEqual(report['sqlite_backups'], 1)
            with closing(sqlite3.connect(backup / 'odysseus/data/app.db')) as restored:
                self.assertEqual(restored.execute('select text from messages').fetchone()[0], 'preserved conversation')
            self.assertEqual((backup / 'hermes/memory.txt').read_text(), 'remember this')
            manifest = json.loads((backup / 'manifest.json').read_text())
            self.assertTrue(manifest['entries'])
            with self.assertRaises(ValueError):
                create_backup(ody, hermes, ody / 'nested-backup')


if __name__ == '__main__':
    unittest.main()

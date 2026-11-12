"""The odysseus-workspace-grants plugin against the pinned agent's real agent.file_safety (not a stand-in).

Skipped when the managed Webapp source is not on this machine. Each test runs in a fresh interpreter so rebinding
file_safety never leaks between tests.
"""
import json
import os
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PLUGIN = ROOT / 'integrations' / 'hermes-mode' / 'plugins' / 'odysseus-workspace-grants'
AGENT = Path(os.environ.get('LOCALAPPDATA', '')) / 'Odysseus' / 'hermes' / 'managed' / 'webapp' / 'releases' / '427ff712100d' / 'src'


def run(script, home, grants=None):
    env = {k: v for k, v in os.environ.items() if k not in ('PYTHONPATH', 'HERMES_HOME', 'HERMES_WRITE_SAFE_ROOT', 'ODYSSEUS_WORKSPACE_GRANTS')}
    env.update(PYTHONPATH=os.pathsep.join([str(AGENT), str(PLUGIN.parent)]), HERMES_HOME=str(home), HERMES_WRITE_SAFE_ROOT=str(Path(home) / 'env-root'))
    if grants:
        env['ODYSSEUS_WORKSPACE_GRANTS'] = str(grants)
    out = subprocess.run([sys.executable, '-c', textwrap.dedent(script)], capture_output=True, text=True, env=env, timeout=90)
    if out.returncode:
        raise AssertionError(out.stderr[-1500:])
    return json.loads(out.stdout.strip().splitlines()[-1])


PRELUDE = """
import json, os, sys, importlib
sys.path.insert(0, os.environ['PYTHONPATH'].split(os.pathsep)[1])
import importlib.util
spec = importlib.util.spec_from_file_location('ody_wg', os.path.join(os.environ['PYTHONPATH'].split(os.pathsep)[1], 'odysseus-workspace-grants', '__init__.py'))
wg = importlib.util.module_from_spec(spec); spec.loader.exec_module(wg)
from agent import file_safety as fs
class Ctx: pass
"""


@unittest.skipUnless((AGENT / 'agent' / 'file_safety.py').is_file(), 'the pinned managed Webapp source is not installed here')
class WorkspaceGrants(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        base = Path(self.tmp.name).resolve()
        self.home = base / 'home'
        (self.home / 'vault').mkdir(parents=True)
        self.grants = base / 'component.json'
        self.a, self.b, self.outside = base / 'proj-a', base / 'proj-b', base / 'elsewhere'
        for p in (self.a, self.b, self.outside):
            p.mkdir()

    def tearDown(self):
        self.tmp.cleanup()

    def verdicts(self, paths):
        script = PRELUDE + f"""
wg.register(Ctx())
print(json.dumps({{p: fs.is_write_denied(p) for p in {json.dumps([str(p) for p in paths])}}}))
"""
        return run(script, self.home, self.grants)

    def test_no_grants_file_means_nothing_is_writable_even_with_a_permissive_environment(self):
        res = self.verdicts([self.a / 'x.txt', self.outside / 'y.txt'])
        self.assertTrue(all(res.values()), res)

    def test_grant_and_revoke_take_effect_without_a_restart(self):
        script = PRELUDE + f"""
import pathlib, time
grants = pathlib.Path({str(self.grants)!r})
wg.register(Ctx())
a, b, out = {str(self.a / 'f.txt')!r}, {str(self.b / 'f.txt')!r}, {str(self.outside / 'f.txt')!r}
def verdict(): return [fs.is_write_denied(a), fs.is_write_denied(b), fs.is_write_denied(out)]
steps = {{'before': verdict()}}
grants.write_text(json.dumps({{'write_roots': [{str(self.a)!r}]}}))
steps['grant_a'] = verdict()
grants.write_text(json.dumps({{'write_roots': [{str(self.a)!r}, {str(self.b)!r}]}}))
steps['grant_ab'] = verdict()
grants.write_text(json.dumps({{'write_roots': [{str(self.b)!r}]}}))
steps['revoke_a'] = verdict()
grants.write_text(json.dumps({{'write_roots': []}}))
steps['revoke_all'] = verdict()
print(json.dumps(steps))
"""
        steps = run(script, self.home, self.grants)
        self.assertEqual(steps, {'before': [True, True, True], 'grant_a': [False, True, True], 'grant_ab': [False, False, True],
                                 'revoke_a': [True, False, True], 'revoke_all': [True, True, True]})

    def test_unreadable_or_malformed_grants_fail_closed(self):
        script = PRELUDE + f"""
import pathlib
grants = pathlib.Path({str(self.grants)!r})
wg.register(Ctx())
a = {str(self.a / 'f.txt')!r}
out = {{}}
grants.write_text(json.dumps({{'write_roots': [{str(self.a)!r}]}})); out['valid'] = fs.is_write_denied(a)
grants.write_text('{{ not json'); out['corrupt'] = fs.is_write_denied(a)
grants.write_text(json.dumps({{'write_roots': 'oops'}})); out['wrong_type'] = fs.is_write_denied(a)
grants.write_text(json.dumps([1, 2])); out['wrong_shape'] = fs.is_write_denied(a)
grants.unlink(); out['deleted'] = fs.is_write_denied(a)
print(json.dumps(out))
"""
        self.assertEqual(run(script, self.home, self.grants), {'valid': False, 'corrupt': True, 'wrong_type': True, 'wrong_shape': True, 'deleted': True})

    def test_dangerous_grants_in_the_file_are_ignored_and_credential_denials_still_win(self):
        home_dir = str(Path.home())
        script = PRELUDE + f"""
import pathlib
grants = pathlib.Path({str(self.grants)!r})
wg.register(Ctx())
anchor = pathlib.Path({str(self.a)!r}).anchor
grants.write_text(json.dumps({{'write_roots': [anchor, {home_dir!r}, 'relative/dir', 5, {str(self.a)!r}]}}))
probe = {{'drive_root_file': os.path.join(anchor, 'x.txt'), 'profile_file': os.path.join({home_dir!r}, 'x.txt'), 'granted': os.path.join({str(self.a)!r}, 'x.txt'),
         'env_in_grants': os.path.join({str(self.home / 'env-root')!r}, 'x.txt'), 'hermes_state': os.path.join({str(self.home)!r}, 'state.db')}}
print(json.dumps({{k: fs.is_write_denied(v) for k, v in probe.items()}}))
"""
        res = run(script, self.home, self.grants)
        self.assertEqual(res, {'drive_root_file': True, 'profile_file': True, 'granted': False, 'env_in_grants': True, 'hermes_state': True})

    def test_junctions_and_dotdot_cannot_escape_a_grant(self):
        link = self.a / 'link'
        made = subprocess.run(['cmd', '/c', 'mklink', '/J', str(link), str(self.outside)], capture_output=True, text=True) if os.name == 'nt' else None
        if os.name != 'nt':
            os.symlink(self.outside, link)
        elif made.returncode:
            self.skipTest('cannot create a junction here')
        self.grants.write_text(json.dumps({'write_roots': [str(self.a)]}))
        res = self.verdicts([self.a / 'ok.txt', self.a / 'link' / 'escape.txt', str(self.a) + os.sep + '..' + os.sep + 'elsewhere' + os.sep + 'e.txt', str(self.a) + '-sibling' + os.sep + 'x.txt'])
        values = list(res.values())
        self.assertEqual(values, [False, True, True, True], res)

    def test_status_file_reports_installed_and_not_installed(self):
        script = PRELUDE + "wg.register(Ctx()); print(json.dumps(json.load(open(os.path.join(os.environ['HERMES_HOME'], wg.STATUS_NAME)))))"
        status = run(script, self.home, self.grants)
        self.assertTrue(status['installed'] and not status['grants_readable'] and status['grants'] is None, status)
        self.grants.write_text(json.dumps({'write_roots': [str(self.a)]}))
        status = run(script, self.home, self.grants)
        self.assertTrue(status['installed'] and status['grants'] == 1, status)
        broken = PRELUDE + "fs.get_safe_write_roots = None\nwg.register(Ctx()); print(json.dumps(json.load(open(os.path.join(os.environ['HERMES_HOME'], wg.STATUS_NAME)))))"
        status = run(broken, self.home, self.grants)
        self.assertFalse(status['installed'])
        self.assertIn('missing', status['reason'])


if __name__ == '__main__':
    unittest.main()

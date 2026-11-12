"""Managed Webapp component: ensure/repair heal instead of rebuilding, per-gateway write roots, deliberate Claude/Codex login use."""
import json
import os
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from src.hermes_component import ComponentError
from src.hermes_webapp_component import LOGIN_TOOLS, WebappComponent


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        base = Path(self.tmp.name)
        self.profile = base / 'profile'
        self.profile.mkdir()
        patcher = mock.patch.object(Path, 'home', return_value=self.profile)         # the temp folder lives under the real AppData
        patcher.start()
        self.addCleanup(patcher.stop)
        self.root = base / 'component'
        self.home = base / 'gateway-home'
        self.agent = base / 'agent'
        (self.agent / 'hermes_cli').mkdir(parents=True)
        (self.agent / 'hermes_cli' / 'config_defaults.py').write_text('X = {"_config_version": 53}\n', encoding='utf-8')
        (self.root / 'managed' / 'webapp' / 'releases').mkdir(parents=True)
        self.home.mkdir()
        (self.home / 'config.yaml').write_text('_config_version: 53\nwebapp:\n  port: 19999\n', encoding='utf-8')
        self.env = {'LOCALAPPDATA': str(base / 'local'), 'SystemRoot': str(base / 'windows'), 'HERMES_WRITE_SAFE_ROOT': r'C:\operator-broad'}
        self.component = WebappComponent(self.root, application_root=ROOT, environment=self.env)
        self.config = {'version': 1, 'kind': 'webapp', 'enabled': True, 'origin': 'managed', 'url': 'http://127.0.0.1:19999/', 'home': str(self.home),
                       'python': sys.executable, 'agent': str(self.agent), 'bundle': None, 'release': 'a' * 12, 'previous': 'b' * 12, 'public_url': ''}
        self.save(self.config)

    def save(self, config):
        self.component.config_path.write_text(json.dumps(config), encoding='utf-8')
        self.config = config

    def repo(self, *parts):
        folder = Path(self.tmp.name).joinpath(*parts)
        (folder / '.git').mkdir(parents=True)
        return folder

    def release(self, name, state):
        folder = self.component.directory / 'releases' / name
        folder.mkdir(parents=True)
        (folder / 'release.json').write_text(json.dumps({'state': state}), encoding='utf-8')
        return folder


class GatewayEnvironment(Base):
    def test_claude_and_codex_logins_are_isolated_by_default(self):
        env = self.component.gateway_environment(self.config)
        for tool, variable in LOGIN_TOOLS.items():
            folder = Path(env[variable])
            self.assertEqual(os.path.realpath(folder), os.path.realpath(self.root / 'managed' / 'webapp' / 'isolated-logins' / tool))
            self.assertTrue(folder.is_dir() and {item.name for item in folder.iterdir()} <= {'.odysseus-private'})        # empty apart from the folder's own marker
        self.assertEqual(self.component.logins(), {'claude': 'isolated', 'codex': 'isolated'})

    def test_allowing_one_login_leaves_only_that_one_visible(self):
        self.assertEqual(self.component.set_logins(allow=['claude']), {'claude': 'allowed', 'codex': 'isolated'})
        env = self.component.gateway_environment(self.component.config())
        self.assertNotIn('CLAUDE_CONFIG_DIR', env)
        self.assertIn('CODEX_HOME', env)
        self.assertEqual(self.component.set_logins(deny=['claude']), {'claude': 'isolated', 'codex': 'isolated'})

    def test_an_allowed_login_follows_the_users_own_custom_location(self):
        self.component.environment['CODEX_HOME'] = r'D:\my-codex'
        self.component.set_logins(allow=['codex'])
        self.assertEqual(self.component.gateway_environment(self.component.config())['CODEX_HOME'], r'D:\my-codex')

    def test_unknown_login_name_is_refused_and_nothing_else_leaks_into_the_gateway(self):
        with self.assertRaises(ComponentError):
            self.component.set_logins(allow=['claude', 'gh'])
        self.component.environment.update({'ANTHROPIC_API_KEY': 'k', 'OPENAI_API_KEY': 'k', 'GH_TOKEN': 'k'})
        env = self.component.gateway_environment(self.config)
        self.assertFalse({'ANTHROPIC_API_KEY', 'OPENAI_API_KEY', 'GH_TOKEN'} & set(env))

    def test_gateway_never_inherits_the_operator_write_policy_and_points_at_the_one_grants_file(self):
        sentinel = str(self.component.directory / 'no-workspace')
        for roots in ((), (self.repo('projects', 'a'), self.repo('projects', 'b'))):
            if roots:
                self.component.set_write_roots(add=[str(r) for r in roots])
            env = self.component.gateway_environment(self.component.config())
            self.assertEqual(env['HERMES_WRITE_SAFE_ROOT'], sentinel, 'the environment is always the empty folder; grants are live, not baked in')
            self.assertNotIn('operator-broad', env['HERMES_WRITE_SAFE_ROOT'])
            self.assertEqual(env['ODYSSEUS_WORKSPACE_GRANTS'], str(self.component.config_path))
        self.assertEqual(os.listdir(sentinel), ['.odysseus-private'], 'the deny folder holds nothing but the component\'s own marker')
        self.assertEqual(len(self.component.write_roots()), 2)
        self.component.set_write_roots(clear=True)
        self.assertEqual(self.component.write_roots(), [])
        self.assertEqual(self.component.gateway_environment(self.component.config())['HERMES_WRITE_SAFE_ROOT'], sentinel)


class WriteRoots(Base):
    def test_dangerous_roots_are_refused(self):
        base = Path(self.tmp.name)
        for bad in (Path(base.anchor), Path.home(), Path.home().parent, self.root, self.root / 'managed', self.home, base, base / 'does-not-exist', 'relative\\folder'):
            with self.assertRaises(ComponentError, msg=str(bad)):
                self.component.set_write_roots(add=[str(bad)])
        self.assertEqual(self.component.write_roots(), [])

    def test_only_the_exact_top_folder_of_a_repository_is_accepted(self):
        parent = Path(self.tmp.name) / 'Project'
        child = self.repo('Project', 'App')
        (child / 'src').mkdir()
        for bad in (parent, child / 'src'):
            with self.assertRaises(ComponentError, msg=str(bad)) as caught:
                self.component.set_write_roots(add=[str(bad)])
            self.assertIn('top folder of a Git repository', str(caught.exception))
        self.assertEqual(self.component.set_write_roots(add=[str(child)]), [os.path.normpath(str(child))])

    def test_the_live_installation_and_login_folders_can_never_be_roots(self):
        live = self.repo('live-odysseus')
        worktree = Path(self.tmp.name) / 'live-odysseus' / 'integration' / 'wt'
        worktree.mkdir(parents=True)
        admin = live / '.git' / 'worktrees' / 'wt'
        admin.mkdir(parents=True)
        (worktree / '.git').write_text(f'gitdir: {admin}\n', encoding='utf-8')
        self.component.application_root = worktree
        profile = Path(self.tmp.name) / 'profile'
        (profile / '.codex' / '.git').mkdir(parents=True)
        (profile / '.claude' / 'proj' / '.git').mkdir(parents=True)
        (profile / 'AppData' / 'Local' / 'x' / '.git').mkdir(parents=True)
        ok = self.repo('profile', 'source', 'repos', 'adkar')
        with mock.patch.object(Path, 'home', return_value=profile):
            for bad in (live, worktree, profile / '.codex', profile / '.claude' / 'proj', profile / 'AppData' / 'Local' / 'x', profile):
                with self.assertRaises(ComponentError, msg=str(bad)):
                    self.component.set_write_roots(add=[str(bad)])
            self.assertEqual(self.component.set_write_roots(add=[str(ok)]), [os.path.normpath(str(ok))])

    def test_inspection_is_read_only_and_describes_worktrees_and_btrfs(self):
        main = self.repo('main-repo')
        (main / '.git' / 'HEAD').write_text('ref: refs/heads/codex/windows-tauri\n', encoding='utf-8')
        admin = main / '.git' / 'worktrees' / 'Mobile'
        admin.mkdir(parents=True)
        (admin / 'HEAD').write_text('ref: refs/heads/codex/android-mobile\n', encoding='utf-8')
        linked = Path(self.tmp.name) / 'elsewhere' / 'Mobile'
        linked.mkdir(parents=True)
        (linked / '.git').write_text(f'gitdir: {admin}\n', encoding='utf-8')
        snapshot = lambda: sorted((str(p), p.stat().st_mtime_ns) for p in Path(self.tmp.name).rglob('*'))
        before = snapshot()
        with mock.patch.object(WebappComponent, '_filesystem', return_value='Btrfs'):
            plain = self.component.inspect_root(main)
            worktree = self.component.inspect_root(linked)
        self.assertEqual(snapshot(), before)
        self.assertEqual((plain['kind'], plain['branch'], plain['problem']), ('repository', 'codex/windows-tauri', None))
        self.assertEqual((worktree['kind'], worktree['branch'], worktree['problem']), ('linked worktree', 'codex/android-mobile', None))
        self.assertTrue(any('OUTSIDE the folder' in note for note in worktree['notes']))
        self.assertTrue(all(any('Btrfs' in note for note in r['notes']) for r in (plain, worktree)))
        plain_folder = Path(self.tmp.name) / 'plain'
        plain_folder.mkdir()
        self.assertIn('not the top folder', self.component.inspect_root(plain_folder)['problem'])

    def test_adding_removing_and_duplicates(self):
        project = self.repo('work', 'p')
        self.component.set_write_roots(add=[str(project)])
        self.component.set_write_roots(add=[str(project).upper()])
        self.assertEqual(len(self.component.write_roots()), 1)
        self.assertEqual(self.component.set_write_roots(remove=[str(project)]), [])
        self.assertNotIn('write_roots', self.component.config() or {})

    def test_an_adopted_gateway_has_no_settings_to_change(self):
        self.save({**self.config, 'origin': 'adopted'})
        with self.assertRaises(ComponentError):
            self.component.set_write_roots(clear=True)
        with self.assertRaises(ComponentError):
            self.component.set_logins(allow=['claude'])


class EnsureAndRepair(Base):
    def run_ensure(self, **kwargs):
        with mock.patch.object(WebappComponent, 'validate', side_effect=kwargs.pop('validate', None)) as validate, \
                mock.patch.object(WebappComponent, '_rebuild', return_value=self.config) as rebuild, \
                mock.patch.object(WebappComponent, '_heal', side_effect=lambda config, **_: config) as heal:
            result = self.component.ensure('managed', **kwargs)
        return validate, rebuild, heal, result

    def test_ensure_never_rebuilds_an_installed_component(self):
        validate, rebuild, heal, _ = self.run_ensure()
        rebuild.assert_not_called()
        heal.assert_called_once()

    def test_repair_of_a_healthy_release_does_not_rebuild(self):
        validate, rebuild, heal, _ = self.run_ensure(repair=True)
        rebuild.assert_not_called()
        self.assertTrue(heal.call_args.kwargs['allow_move'])
        self.assertIn('nothing was rebuilt', ' '.join(self.component.last_actions))

    def test_repair_rebuilds_when_the_release_no_longer_verifies(self):
        validate, rebuild, heal, _ = self.run_ensure(repair=True, validate=ComponentError('The Webapp bundle is incomplete'))
        rebuild.assert_called_once()
        self.assertIn('rebuilding', ' '.join(self.component.last_actions))

    def test_rebuild_flag_forces_a_new_release(self):
        validate, rebuild, heal, _ = self.run_ensure(repair=True, rebuild=True)
        rebuild.assert_called_once()
        validate.assert_not_called()

    def test_a_new_release_keeps_the_users_settings(self):
        self.save({**self.config, 'write_roots': ['D:\\p'], 'logins': ['claude']})
        fresh = {k: v for k, v in self.config.items() if k not in ('write_roots', 'logins')}
        with mock.patch.object(WebappComponent, '_ensure_managed', return_value=fresh), mock.patch.object(WebappComponent, '_sweep_releases'):
            candidate = self.component._rebuild(mock.Mock(), 'managed', self.config, None, None, None, None, '', None, True)
        self.assertEqual((candidate['write_roots'], candidate['logins']), (['D:\\p'], ['claude']))
        self.assertEqual(json.loads(self.component.config_path.read_text(encoding='utf-8'))['logins'], ['claude'])


class Heal(Base):
    def test_a_missing_gateway_home_is_recreated_without_building(self):
        import shutil
        shutil.rmtree(self.home)
        healed = self.component._heal(self.config)
        text = (self.home / 'config.yaml').read_text(encoding='utf-8')
        self.assertIn('port: 19999', text)
        self.assertIn('mode: manual', text)                                               # a fresh home asks before acting
        self.assertTrue((self.home / 'desktop-plugins' / 'odysseus-return' / 'plugin.js').is_file())
        self.assertIn('gateway home recreated', self.component.last_actions)
        self.assertEqual(healed['release'], self.config['release'])

    def test_a_healthy_home_is_left_alone(self):
        before = (self.home / 'config.yaml').read_text(encoding='utf-8')
        self.component._heal(self.config)
        self.assertEqual((self.home / 'config.yaml').read_text(encoding='utf-8'), before)
        self.assertFalse([p for p in self.home.iterdir() if 'pre-odysseus' in p.name])

    def test_ensure_ignores_a_different_home_or_port_visibly_and_only_repair_moves_it(self):
        kept = self.component._heal(self.config, port=20001)
        self.assertEqual(kept['url'], self.config['url'])
        self.assertIn('ignored the given home or port', ' '.join(self.component.last_actions))
        kept = self.component._heal(self.config, home=str(self.home.parent / 'other-home'))
        self.assertEqual(kept['home'], self.config['home'])
        self.assertFalse((self.home.parent / 'other-home').exists())
        with mock.patch.object(WebappComponent, '_hermes_yaml') as yaml:
            moved = self.component._heal(self.config, port=20001, allow_move=True)
        self.assertEqual(moved['url'], 'http://127.0.0.1:20001/')
        yaml.assert_called_once()                                                        # the existing config is edited by Hermes' own YAML library, with a backup
        self.assertTrue([p for p in self.home.iterdir() if 'pre-odysseus' in p.name])

    def test_a_new_home_is_created_when_asked(self):
        new = self.home.parent / 'new-home'
        moved = self.component._heal(self.config, home=str(new), allow_move=True)
        self.assertEqual(os.path.realpath(moved['home']), os.path.realpath(new))
        self.assertTrue((new / 'config.yaml').is_file())
        self.assertEqual(os.path.realpath(json.loads(self.component.config_path.read_text(encoding='utf-8'))['home']), os.path.realpath(new))

    def test_changing_public_url_is_not_a_quiet_heal(self):
        with self.assertRaises(ComponentError):
            self.component._heal(self.config, public_url='http://hermes.localhost:7000')


class Sweep(Base):
    def test_unfinished_and_orphaned_releases_go_but_current_previous_and_recent_failures_stay(self):
        current, previous = self.release('a' * 12, 'ready'), self.release('b' * 12, 'ready')
        preparing, orphan = self.release('c' * 12, 'preparing'), self.release('d' * 12, 'ready')
        failed = [self.release(f'{i}' * 12, 'failed') for i in range(1, 5)]
        for index, folder in enumerate(failed):
            os.utime(folder, (1000 + index, 1000 + index))
        locked = preparing / 'src' / 'objects'
        locked.mkdir(parents=True)
        (locked / 'pack').write_text('x')
        os.chmod(locked / 'pack', stat.S_IREAD)                                           # git leaves read-only files behind
        self.component._sweep_releases(self.config)
        self.assertTrue(current.exists() and previous.exists())
        self.assertFalse(preparing.exists() or orphan.exists())
        self.assertEqual(sorted(p.name for p in (self.component.directory / 'releases').iterdir() if p.name not in (current.name, previous.name)), [failed[2].name, failed[3].name])

    def test_foreign_folders_are_never_touched(self):
        base = self.component.directory / 'releases'
        (base / 'not-a-release').mkdir()
        (base / 'not-a-release' / 'keep.txt').write_text('mine')
        self.component._sweep_releases(self.config)
        self.assertTrue((base / 'not-a-release' / 'keep.txt').exists())


def _hermes_runtime_python():
    """A python with ruamel.yaml (the component's yaml helper runs the Hermes runtime, not the test interpreter)."""
    local = os.environ.get('LOCALAPPDATA', '')
    candidates = [os.environ.get('HERMES_PYTHON'),
                  str(Path(local) / 'hermes' / 'installs' / '562e58e2ea9aca80' / 'environments' / '6eff8cc60cb848909fbabec0b09c88c2' / 'venv' / 'Scripts' / 'python.exe')]
    for candidate in candidates:
        if candidate and Path(candidate).is_file():
            probe = subprocess.run([candidate, '-c', 'import ruamel.yaml'], capture_output=True)
            if probe.returncode == 0:
                return candidate
    return None


@unittest.skipUnless(_hermes_runtime_python(), 'no Hermes runtime python with ruamel.yaml is installed here')
class WorkspaceGrantsPluginInstall(Base):
    def setUp(self):
        super().setUp()
        self.runtime = _hermes_runtime_python()
        self.config['python'] = self.runtime
        self.save(self.config)

    def test_install_copies_the_plugin_and_merges_it_into_plugins_enabled(self):
        self.component.install_workspace_grants_plugin(self.home, self.runtime)
        target = self.home / 'plugins' / 'odysseus-workspace-grants'
        self.assertTrue((target / 'plugin.yaml').is_file() and (target / '__init__.py').is_file())
        settings = self.component.home_settings(self.runtime, self.home)
        self.assertEqual(settings['plugins_enabled'], ['odysseus-workspace-grants'])
        self.component.install_workspace_grants_plugin(self.home, self.runtime)   # idempotent
        settings = self.component.home_settings(self.runtime, self.home)
        self.assertEqual(settings['plugins_enabled'].count('odysseus-workspace-grants'), 1)

    def test_existing_plugins_are_preserved_and_a_foreign_plugin_is_never_overwritten(self):
        (self.home / 'config.yaml').write_text('_config_version: 53\nplugins:\n  enabled:\n    - agent-importers\n', encoding='utf-8')
        self.component.install_workspace_grants_plugin(self.home, self.runtime)
        settings = self.component.home_settings(self.runtime, self.home)
        self.assertEqual(settings['plugins_enabled'], ['agent-importers', 'odysseus-workspace-grants'])
        target = self.home / 'plugins' / 'odysseus-workspace-grants'
        (target / 'plugin.yaml').write_text('name: something-else\n', encoding='utf-8')
        with self.assertRaises(ComponentError):
            self.component.install_workspace_grants_plugin(self.home, self.runtime)


if __name__ == '__main__':
    unittest.main()

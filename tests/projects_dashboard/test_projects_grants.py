import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from src import projects_dashboard as pd  # noqa: E402
from src.hermes_webapp_component import WebappComponent  # noqa: E402
from src.projects_grants import GrantError, GrantManager  # noqa: E402

GIT = pd._git_exe()
pytestmark = pytest.mark.skipif(not GIT, reason='git is required')


def run_git(cwd, *args):
    subprocess.run([GIT, '-C', str(cwd), '-c', 'user.name=t', '-c', 'user.email=t@t', *args], check=True, capture_output=True)


class Clock:
    def __init__(self):
        self.now = 1_800_000_000.0

    def __call__(self):
        return self.now


@pytest.fixture
def world(tmp_path, monkeypatch):
    fake_home = tmp_path / 'home' / 'user'
    fake_home.mkdir(parents=True)
    monkeypatch.setattr(Path, 'home', classmethod(lambda cls: fake_home))
    env = {'LOCALAPPDATA': str(fake_home / 'AppData' / 'Local'), 'SystemRoot': r'C:\Windows'}
    repo = tmp_path / 'work' / 'app'
    repo.mkdir(parents=True)
    run_git(repo, 'init', '-q', '-b', 'main')
    (repo / 'a.txt').write_text('x')
    run_git(repo, 'add', '.')
    run_git(repo, 'commit', '-q', '-m', 'first')
    tree = tmp_path / 'work' / 'app-feature'
    run_git(repo, 'worktree', 'add', '-q', '-b', 'feature/x', str(tree))
    other = tmp_path / 'work' / 'other'
    other.mkdir()
    run_git(other, 'init', '-q', '-b', 'main')
    plain = tmp_path / 'work' / 'plain'
    plain.mkdir()
    webui = tmp_path / 'webui'
    (webui / 'sessions').mkdir(parents=True)
    (webui / 'projects.json').write_text(json.dumps([{'project_id': 'p1', 'name': 'App'}]))
    (webui / 'workspaces.json').write_text(json.dumps([{'path': str(repo), 'name': 'app'}, {'path': str(plain), 'name': 'plain'}, {'path': str(other), 'name': 'other'}]))
    (webui / 'sessions' / '_index.json').write_text(json.dumps([{'session_id': 's', 'title': 't', 'workspace': str(tree), 'project_id': 'p1', 'updated_at': 1}]))
    root = tmp_path / 'component'
    (root / 'managed' / 'webapp').mkdir(parents=True)
    config = {'version': 1, 'kind': 'webapp', 'enabled': True, 'origin': 'managed', 'agent': str(tmp_path / 'agent'), 'python': 'python', 'home': str(tmp_path / 'gwhome'),
              'url': 'http://127.0.0.1:9130/', 'write_roots': [str(other)]}
    (root / 'managed' / 'webapp' / 'component.json').write_text(json.dumps(config))
    component = WebappComponent(root, application_root=tmp_path / 'odysseus-app', environment=env)
    dash = pd.Dashboard(tmp_path / 'gwhome', webui, root, None, environment={})
    clock = Clock()
    manager = GrantManager(dash, lambda: component, root / 'managed' / 'webapp' / 'grant-audit.jsonl', clock=clock)
    project = next(p['id'] for p in dash.projects() if p['name'] == 'App')
    return manager, component, dash, project, repo, tree, other, plain, clock


class TestPreview:
    def test_worktree_grant_shows_canonical_path_warnings_and_a_confirmation(self, world):
        manager, component, dash, project, repo, tree, *_ = world
        report = manager.preview('grant', str(tree), project, 'admin')
        assert report['problems'] == [] and report['confirmation'] and report['typed_name'] == 'app-feature'
        assert pd.norm(report['canonical']) == pd.norm(os.path.realpath(tree))
        assert report['worktree']['kind'] == 'worktree'
        text = ' '.join(report['warnings'])
        assert 'Git data' in text and 'share that Git data' in text
        assert 'not OS containment' in report['note']

    def test_preview_never_changes_the_grant_list(self, world):
        manager, component, dash, project, repo, tree, other, *_ = world
        manager.preview('grant', str(tree), project, 'admin')
        manager.preview('revoke', str(other), project, 'admin')
        assert component.write_roots() == [str(other)] and not manager.audit_path.exists()

    @pytest.mark.parametrize('which', ['plain', 'parent', 'unknown_repo'])
    def test_folders_that_must_not_be_granted_are_refused_with_a_reason(self, world, which, tmp_path):
        manager, component, dash, project, repo, tree, other, plain, _ = world
        path = {'plain': plain, 'parent': repo.parent, 'unknown_repo': other}[which]
        report = manager.preview('grant', str(path), project, 'admin')
        assert report['problems'] and 'confirmation' not in report

    def test_a_valid_repository_that_is_not_part_of_the_project_is_refused(self, world, tmp_path):
        manager, _, _, project, *_ = world
        stranger = tmp_path / 'work' / 'stranger'
        stranger.mkdir()
        run_git(stranger, 'init', '-q', '-b', 'main')
        report = manager.preview('grant', str(stranger), project, 'admin')
        assert any('not one of the registered' in p for p in report['problems']) and 'confirmation' not in report

    def test_the_user_profile_and_a_drive_root_are_refused(self, world):
        manager, component, dash, project, *_ = world
        for path in (str(Path.home()), os.path.splitdrive(str(Path.home()))[0] + '\\', str(Path.home() / '.ssh')):
            report = manager.preview('grant', path, project, 'admin')
            assert report['problems'] and 'confirmation' not in report

    def test_relative_paths_and_bad_actions_are_rejected(self, world):
        manager, _, _, project, *_ = world
        with pytest.raises(GrantError) as error:
            manager.preview('grant', 'relative/path', project, 'admin')
        assert error.value.status == 422
        with pytest.raises(GrantError):
            manager.preview('everything', str(world[5]), project, 'admin')

    def test_unknown_project_is_404(self, world):
        manager, _, _, _, _, tree, *_ = world
        with pytest.raises(GrantError) as error:
            manager.preview('grant', str(tree), 'ffffffffff', 'admin')
        assert error.value.status == 404

    def test_revoking_something_that_is_not_an_exact_grant_is_refused(self, world):
        manager, component, dash, project, repo, tree, other, *_ = world
        assert manager.preview('revoke', str(tree), project, 'admin')['problems']
        assert manager.preview('revoke', str(other.parent), project, 'admin')['problems']      # a parent of a grant is not a grant


class TestApply:
    def test_grant_needs_the_typed_name_then_adds_exactly_one_root_and_audits(self, world):
        manager, component, dash, project, repo, tree, other, *_ = world
        token = manager.preview('grant', str(tree), project, 'admin')['confirmation']
        result = manager.apply(token, 'app-feature', 'admin', remote='127.0.0.1')
        assert sorted(pd.norm(r) for r in result['roots']) == sorted(pd.norm(r) for r in (str(other), os.path.realpath(tree)))
        assert component.write_roots()[0] == str(other)                       # the existing grant is untouched and first
        record = manager.audit()[0]
        assert record['result'] == 'applied' and record['action'] == 'grant' and record['user'] == 'admin' and record['before'] == [str(other)] and len(record['after']) == 2

    def test_wrong_typed_name_refuses_and_burns_the_confirmation(self, world):
        manager, component, dash, project, repo, tree, other, *_ = world
        token = manager.preview('grant', str(tree), project, 'admin')['confirmation']
        with pytest.raises(GrantError) as error:
            manager.apply(token, 'wrong', 'admin')
        assert error.value.status == 422 and component.write_roots() == [str(other)]
        with pytest.raises(GrantError):
            manager.apply(token, 'app-feature', 'admin')                          # single use
        assert manager.audit()[0]['result'].startswith('refused')

    def test_confirmation_expires_and_is_bound_to_the_user(self, world):
        manager, component, dash, project, repo, tree, other, plain, clock = world
        token = manager.preview('grant', str(tree), project, 'admin')['confirmation']
        clock.now += 121
        with pytest.raises(GrantError):
            manager.apply(token, 'app-feature', 'admin')
        token = manager.preview('grant', str(tree), project, 'admin')['confirmation']
        with pytest.raises(GrantError) as error:
            manager.apply(token, 'app-feature', 'someone-else')
        assert error.value.status == 403 and component.write_roots() == [str(other)]

    def test_a_changed_grant_list_invalidates_the_preview(self, world):
        manager, component, dash, project, repo, tree, other, *_ = world
        token = manager.preview('grant', str(tree), project, 'admin')['confirmation']
        component.set_write_roots(add=[str(repo)])
        with pytest.raises(GrantError):
            manager.apply(token, 'app-feature', 'admin')
        assert str(tree) not in component.write_roots()

    def test_revoke_removes_only_the_exact_root_and_never_clears(self, world):
        manager, component, dash, project, repo, tree, other, *_ = world
        calls = []
        original = component.set_write_roots
        component.set_write_roots = lambda **kw: (calls.append(kw), original(**kw))[1]
        manager.apply(manager.preview('grant', str(tree), project, 'admin')['confirmation'], 'app-feature', 'admin')
        token = manager.preview('revoke', str(tree), project, 'admin')['confirmation']
        manager.apply(token, '', 'admin')
        assert component.write_roots() == [str(other)]
        assert all(set(kw) in ({'add'}, {'remove'}) and not kw.get('clear') for kw in calls)
        assert [r['action'] for r in manager.audit()] == ['revoke', 'grant']

    def test_revoking_the_last_grant_leaves_no_roots_not_an_inherited_wildcard(self, world):
        manager, component, dash, project, repo, tree, other, *_ = world
        manager.apply(manager.preview('revoke', str(other), project, 'admin')['confirmation'], '', 'admin')
        assert component.write_roots() == []
        assert 'write_roots' not in json.loads(component.config_path.read_text(encoding='utf-8'))

    def test_a_component_that_is_not_managed_cannot_be_changed(self, world, tmp_path):
        manager, component, dash, project, repo, tree, *_ = world
        data = json.loads(component.config_path.read_text(encoding='utf-8'))
        data['origin'] = 'adopted'
        component.config_path.write_text(json.dumps(data))
        with pytest.raises(GrantError):
            manager.preview('grant', str(tree), project, 'admin')


def test_routes_use_the_write_guard_for_changes_and_the_read_guard_for_the_audit(tmp_path):
    fastapi = pytest.importorskip('fastapi')
    from fastapi.testclient import TestClient
    from routes.projects_dashboard_routes import make_projects_dashboard_router

    def deny(request):
        raise fastapi.HTTPException(403, 'nope')

    class Boom:
        def __getattr__(self, name):
            raise AssertionError('the guard must run before any grant logic')

    app = fastapi.FastAPI()
    app.include_router(make_projects_dashboard_router(lambda: Boom(), guard=lambda r: None, get_grants=lambda: Boom(), write_guard=deny, grants_enabled=lambda: True))
    client = TestClient(app)
    assert client.post('/api/projects-dashboard/grants/preview', json={'action': 'grant', 'path': 'F:\\x'}).status_code == 403
    assert client.post('/api/projects-dashboard/grants/apply', json={'confirmation': 'x'}).status_code == 403


class TestFeatureSwitch:
    def client(self, **kw):
        fastapi = pytest.importorskip('fastapi')
        from fastapi.testclient import TestClient
        from routes.projects_dashboard_routes import make_projects_dashboard_router

        calls = []

        class Grants:
            def preview(self, *a):
                calls.append('preview')
                return {'ok': True}

            def apply(self, *a, **k):
                calls.append('apply')
                return {'ok': True}

            def audit(self, limit):
                return []

        class Dash:
            def overview(self):
                return {'projects': []}

        app = fastapi.FastAPI()
        app.include_router(make_projects_dashboard_router(lambda: Dash(), guard=lambda r: None, get_grants=lambda: Grants(), write_guard=lambda r: None, **kw))
        return TestClient(app), calls

    PREVIEW = {'action': 'grant', 'path': 'F:\\x', 'project_id': ''}

    def test_the_switch_is_off_by_default_and_rejects_direct_calls(self, monkeypatch):
        monkeypatch.delenv('ODYSSEUS_PROJECTS_GRANTS', raising=False)
        client, calls = self.client()
        assert client.post('/api/projects-dashboard/grants/preview', json=self.PREVIEW).status_code == 403
        assert client.post('/api/projects-dashboard/grants/apply', json={'confirmation': 'x', 'typed_name': 'x'}).status_code == 403
        assert calls == [] and client.get('/api/projects-dashboard').json()['grants'] == {'available': True, 'enabled': False, 'switch': 'ODYSSEUS_PROJECTS_GRANTS'}
        for value in ('0', '', 'no', 'false', 'maybe'):
            monkeypatch.setenv('ODYSSEUS_PROJECTS_GRANTS', value)
            assert client.post('/api/projects-dashboard/grants/preview', json=self.PREVIEW).status_code == 403

    def test_switch_on_with_authentication_reaches_the_grant_logic(self, monkeypatch):
        monkeypatch.setenv('ODYSSEUS_PROJECTS_GRANTS', '1')
        client, calls = self.client(auth_is_off=lambda: False)
        assert client.post('/api/projects-dashboard/grants/preview', json=self.PREVIEW).status_code == 200
        assert client.get('/api/projects-dashboard').json()['grants']['enabled'] is True and calls == ['preview']

    def test_switch_on_but_authentication_disabled_still_fails_closed(self, monkeypatch):
        monkeypatch.setenv('ODYSSEUS_PROJECTS_GRANTS', 'true')
        client, calls = self.client(auth_is_off=lambda: True)
        assert client.post('/api/projects-dashboard/grants/preview', json=self.PREVIEW).status_code == 403
        assert client.post('/api/projects-dashboard/grants/apply', json={'confirmation': 'x'}).status_code == 403 and calls == []

    def test_production_wiring_leaves_the_switch_and_auth_check_at_their_safe_defaults(self):
        import inspect
        from routes import projects_dashboard_routes as routes
        parameters = inspect.signature(routes.make_projects_dashboard_router).parameters
        assert parameters['grants_enabled'].default is routes.grants_switch and parameters['auth_is_off'].default is routes._fail_closed_without_auth
        source = (Path(__file__).resolve().parents[2] / 'app.py').read_text(encoding='utf-8')
        wiring = [line for line in source.splitlines() if 'make_projects_dashboard_router(' in line and 'import' not in line][0]
        assert 'grants_enabled' not in wiring and 'auth_is_off' not in wiring and 'write_guard' not in wiring and 'guard=' not in wiring


@pytest.mark.skipif(os.name != 'nt', reason='NTFS junctions')
class TestJunctions:
    def junction(self, link, target):
        done = subprocess.run(['cmd', '/c', 'mklink', '/J', str(link), str(target)], capture_output=True, text=True)
        if done.returncode:
            pytest.skip('cannot create a junction here: ' + done.stdout + done.stderr)

    def test_a_junction_inside_a_granted_folder_that_leads_elsewhere_is_not_covered(self, tmp_path):
        granted, outside = tmp_path / 'granted', tmp_path / 'outside'
        granted.mkdir()
        outside.mkdir()
        self.junction(granted / 'link', outside)
        dash = pd.Dashboard(None, None, None, None, environment={})
        assert dash.permission(str(granted / 'link'), [str(granted)], [])['state'] == 'none'
        assert dash.permission(str(granted), [str(granted)], [])['state'] == 'granted'

    def test_a_junction_to_an_ungranted_project_folder_gets_that_folders_real_state(self, tmp_path):
        real, link = tmp_path / 'real', tmp_path / 'via'
        real.mkdir()
        self.junction(link, real)
        dash = pd.Dashboard(None, None, None, None, environment={})
        assert dash.permission(str(link), [str(real)], [])['state'] == 'granted'
        assert dash.permission(str(link), [], [str(real)])['state'] == 'blocked'

    def test_grant_through_a_junction_resolves_to_the_real_folder_and_a_foreign_target_is_refused(self, world, tmp_path):
        manager, component, dash, project, repo, tree, other, plain, _ = world
        link = tmp_path / 'work' / 'alias-of-feature'
        self.junction(link, tree)
        report = manager.preview('grant', str(link), project, 'admin')
        assert pd.norm(report['canonical']) == pd.norm(os.path.realpath(tree)) and any('junction or symbolic link' in w for w in report['warnings'])
        stranger = tmp_path / 'work' / 'stranger2'
        stranger.mkdir()
        run_git(stranger, 'init', '-q', '-b', 'main')
        sneaky = tmp_path / 'work' / 'looks-like-feature'
        self.junction(sneaky, stranger)
        refused = manager.preview('grant', str(sneaky), project, 'admin')
        assert refused['problems'] and 'confirmation' not in refused
        token = report['confirmation']
        manager.apply(token, os.path.basename(report['canonical']), 'admin')
        assert [pd.norm(r) for r in component.write_roots() if pd.norm(r) == pd.norm(os.path.realpath(tree))] and all(pd.norm(r) != pd.norm(link) for r in component.write_roots())

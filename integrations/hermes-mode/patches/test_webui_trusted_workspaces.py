"""Trusted Workspaces: approval, denial, revocation and persistence, checked against the REAL Agent file-safety code."""
import os
from pathlib import Path
import subprocess
import sys

import pytest

AGENT = Path(os.environ.get('HERMES_WEBUI_AGENT_DIR') or Path.home() / 'AppData/Local/hermes/hermes-agent')
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from api import trusted_workspaces as tw


@pytest.fixture
def world(tmp_path, monkeypatch):
    home = tmp_path / 'hermes-home'
    (home / 'vault').mkdir(parents=True)
    launch = tmp_path / 'launch-root'
    launch.mkdir()
    monkeypatch.setenv('HERMES_HOME', str(home))
    monkeypatch.setenv('HERMES_WRITE_SAFE_ROOT', str(launch))
    monkeypatch.delenv('HERMES_WEBUI_TRUSTED_WORKSPACES', raising=False)
    monkeypatch.setattr(tw, '_hermes_home', lambda: home)
    original_forbidden = tw._forbidden_ancestors
    monkeypatch.setattr(tw, '_forbidden_ancestors', lambda: [home, Path.home()])
    tw.init()
    yield SimpleWorld(tmp_path, home, launch)
    monkeypatch.undo()


class SimpleWorld:
    def __init__(self, tmp, home, launch):
        self.tmp, self.home, self.launch = tmp, home, launch

    def folder(self, name):
        path = self.tmp / name
        path.mkdir(exist_ok=True)
        return str(path)


def agent_denial(path):
    """The same function the Agent's file tools call before every write."""
    if str(AGENT) not in sys.path:
        sys.path.insert(0, str(AGENT))
    from agent.file_safety import get_write_denied_error
    return get_write_denied_error(str(path))


needs_agent = pytest.mark.skipif(not (AGENT / 'agent/file_safety.py').exists(), reason='needs the installed Hermes Agent')


@needs_agent
def test_actual_agent_write_permission_follows_approve_deny_revoke(world):
    target = Path(world.folder('project')) / 'notes.txt'
    assert 'outside HERMES_WRITE_SAFE_ROOT' in agent_denial(target)                       # not approved: Agent refuses
    assert agent_denial(Path(world.launch) / 'ok.txt') is None                            # launch root still works
    tw.approve(str(target.parent), 'always')
    assert agent_denial(target) is None                                                   # approved: Agent allows, no restart
    assert agent_denial(target.parent / 'sub' / 'deep.txt') is None                       # subfolders included
    assert 'outside HERMES_WRITE_SAFE_ROOT' in agent_denial(Path(world.folder('other')) / 'x.txt')   # a sibling is not
    tw.revoke(str(target.parent))
    assert 'outside HERMES_WRITE_SAFE_ROOT' in agent_denial(target)                       # revoked: refused again immediately


@needs_agent
def test_allow_once_is_session_only_and_always_survives_a_restart(world):
    once, always = Path(world.folder('once')) / 'a', Path(world.folder('always')) / 'a'
    tw.approve(str(once.parent), 'once')
    tw.approve(str(always.parent), 'always')
    assert agent_denial(once) is None and agent_denial(always) is None
    os.environ['HERMES_WRITE_SAFE_ROOT'] = str(world.launch)      # a fresh process starts from the launch environment
    tw.init()
    assert 'outside' in agent_denial(once)
    assert agent_denial(always) is None


@needs_agent
def test_nothing_approved_means_deny_all_never_unrestricted(world, monkeypatch):
    monkeypatch.setenv('HERMES_WEBUI_TRUSTED_WORKSPACES', '1')
    monkeypatch.setenv('HERMES_WRITE_SAFE_ROOT', '')
    assert tw.init() is True
    assert os.environ['HERMES_WRITE_SAFE_ROOT']                        # never empty (empty means "unrestricted" to the Agent)
    assert 'outside' in agent_denial(Path(world.folder('anywhere')) / 'x.txt')
    tw.approve(world.folder('anywhere'), 'always')
    assert agent_denial(Path(world.folder('anywhere')) / 'x.txt') is None
    tw.revoke(world.folder('anywhere'))
    assert os.environ['HERMES_WRITE_SAFE_ROOT'] and 'outside' in agent_denial(Path(world.folder('anywhere')) / 'x.txt')


def test_inactive_without_a_configured_guard_changes_nothing(tmp_path, monkeypatch):
    monkeypatch.delenv('HERMES_WRITE_SAFE_ROOT', raising=False)
    monkeypatch.delenv('HERMES_WEBUI_TRUSTED_WORKSPACES', raising=False)
    monkeypatch.setattr(tw, '_hermes_home', lambda: tmp_path)
    assert tw.init() is False
    assert 'HERMES_WRITE_SAFE_ROOT' not in os.environ
    assert tw.is_trusted(str(tmp_path)) is True
    with pytest.raises(tw.WorkspaceError):
        tw.approve(str(tmp_path), 'always')


def test_launch_roots_are_preserved_and_cannot_be_revoked(world):
    assert world.launch.name in os.environ['HERMES_WRITE_SAFE_ROOT']
    tw.approve(world.folder('p'), 'always')
    assert str(world.launch) in os.environ['HERMES_WRITE_SAFE_ROOT'].split(os.pathsep)
    with pytest.raises(tw.WorkspaceError, match='configured when Hermes starts'):
        tw.revoke(str(world.launch))
    assert tw.approve(str(world.launch), 'always')['result'] == 'already_configured'
    assert tw.status()['always'] and tw.status()['configured'] == [str(world.launch)]


@pytest.mark.parametrize('bad', ['', 'relative\\path', '\\\\server\\share\\x', '\\\\?\\C:\\x', '\\\\.\\pipe\\x', 'C:\\', 'F:\\'])
def test_unsafe_paths_are_refused(world, bad):
    with pytest.raises(tw.WorkspaceError):
        tw.canonicalize(bad)


def test_home_hermes_home_missing_and_file_paths_are_refused(world, tmp_path):
    for forbidden in (str(Path.home()), str(world.home), str(world.home / 'vault'), str(Path.home().parent)):
        with pytest.raises(tw.WorkspaceError):
            tw.canonicalize(forbidden)
    with pytest.raises(tw.WorkspaceError):
        tw.canonicalize(str(tmp_path / 'does-not-exist'))
    afile = tmp_path / 'file.txt'
    afile.write_text('x')
    with pytest.raises(tw.WorkspaceError):
        tw.canonicalize(str(afile))


@pytest.mark.skipif(os.name != 'nt', reason='Windows junctions')
@needs_agent
def test_a_junction_cannot_smuggle_another_folder_in(world, tmp_path):
    secret = Path(world.folder('secret-area'))
    link = tmp_path / 'looks-harmless'
    subprocess.run(['cmd', '/c', 'mklink', '/J', str(link), str(secret)], check=True, capture_output=True)
    assert tw.canonicalize(str(link)) == os.path.realpath(secret)           # the dialog shows the REAL target
    tw.approve(str(link), 'always')
    assert agent_denial(secret / 'x.txt') is None and agent_denial(link / 'x.txt') is None
    tw.revoke(str(link))
    assert 'outside' in agent_denial(link / 'x.txt') and 'outside' in agent_denial(secret / 'x.txt')


@pytest.mark.skipif(os.name != 'nt', reason='Windows junctions')
@needs_agent
def test_an_approved_folder_replaced_by_a_junction_loses_its_approval(world, tmp_path):
    project = tmp_path / 'proj'
    project.mkdir()
    tw.approve(str(project), 'always')
    assert agent_denial(project / 'x.txt') is None
    elsewhere = Path(world.folder('elsewhere'))
    project.rmdir()
    subprocess.run(['cmd', '/c', 'mklink', '/J', str(project), str(elsewhere)], check=True, capture_output=True)
    tw._apply()
    assert 'outside' in agent_denial(elsewhere / 'x.txt'), 'a junction swapped in after approval must not grant its target'
    assert tw.status()['stale'] == [str(project)] or tw.status()['stale']


def test_state_lives_where_the_agent_file_tools_cannot_write(world):
    tw.approve(world.folder('p'), 'always')
    saved = list((world.home / 'vault').glob('webui-trusted-workspaces.json'))
    assert saved, 'approvals are stored under the protected vault path'


def test_corrupt_state_is_kept_and_never_grants_anything(world):
    state = world.home / 'vault' / 'webui-trusted-workspaces.json'
    state.write_text('{not json', encoding='utf-8')
    tw.init()
    assert not tw.status()['always']
    assert (world.home / 'vault' / 'webui-trusted-workspaces.corrupt').exists()


def shadowing_imports(source, functions=('handle_get', 'handle_post')):
    """(function, line, name) for each function-local import that rebinds a module-level name."""
    import ast
    tree = ast.parse(source)
    module_names = {a.asname or a.name.split('.')[0] for n in tree.body if isinstance(n, (ast.Import, ast.ImportFrom)) for a in n.names}
    found = []
    for func in tree.body:
        if isinstance(func, ast.FunctionDef) and func.name in functions:
            for node in ast.walk(func):
                if isinstance(node, (ast.Import, ast.ImportFrom)):
                    found += [(func.name, node.lineno, a.asname or a.name.split('.')[0]) for a in node.names
                              if (a.asname or a.name.split('.')[0]) in module_names]
    return found


def test_route_handlers_do_not_shadow_module_imports_with_local_imports():
    """A function-local `from x import name` makes `name` local for the WHOLE function, breaking every earlier use
    (this once turned GET /api/sessions into HTTP 500 via parse_qs)."""
    source = (Path(__file__).resolve().parents[1] / 'api' / 'routes.py').read_text(encoding='utf-8')
    upstream_baseline = {'quote', 'get_active_profile_name', '_re'}   # already present at the pinned WebUI revision
    assert [f for f in shadowing_imports(source) if f[2] not in upstream_baseline] == []


def test_the_guard_detects_the_original_bug():
    bad = (
        "from urllib.parse import parse_qs",
        "",
        "def handle_get():",
        "    parse_qs('a')",
        "    from urllib.parse import parse_qs",
    )
    assert shadowing_imports(chr(10).join(bad) + chr(10)) == [('handle_get', 5, 'parse_qs')]

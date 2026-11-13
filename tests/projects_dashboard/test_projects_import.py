import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from src import hermes_projects_import as imp  # noqa: E402


def make_state(tmp_path, folders):
    state = tmp_path / 'webui'
    (state / 'sessions').mkdir(parents=True)
    (state / 'projects.json').write_text(json.dumps([
        {'project_id': 'a1', 'name': 'Alpha', 'color': '#7cb9ff'},
        {'project_id': 'b2', 'name': 'Beta', 'color': 'red; DROP'},
        {'project_id': 'c3', 'name': 'Gamma'},
        {'project_id': 'd4', 'name': 'Delta'},
    ]), encoding='utf-8')
    rows = [{'project_id': 'a1', 'workspace': folders['alpha']}, {'project_id': 'a1', 'workspace': folders['alpha']}, {'project_id': 'a1', 'workspace': folders['other']},
            {'project_id': 'b2', 'workspace': folders['beta']}, {'project_id': 'c3', 'workspace': str(tmp_path / 'gone')}, {'workspace': folders['other']}]
    (state / 'sessions' / '_index.json').write_text(json.dumps(rows), encoding='utf-8')
    return state


def folders(tmp_path):
    out = {n: tmp_path / n for n in ('alpha', 'beta', 'other')}
    for p in out.values():
        p.mkdir()
    return {k: str(v) for k, v in out.items()}


def test_legacy_projects_take_the_folder_most_conversations_live_in_and_ignore_bad_data(tmp_path):
    f = folders(tmp_path)
    found = {p['name']: p for p in imp.legacy_projects(make_state(tmp_path, f))}
    assert found['Alpha']['folder'] == f['alpha'] and found['Alpha']['conversations'] == 2 and found['Alpha']['colour'] == '#7cb9ff'
    assert found['Beta']['colour'] is None                                        # not a #rrggbb value: never forwarded
    assert found['Gamma']['folder'] is None and found['Delta']['folder'] is None   # missing folder / no conversations: no guess


def test_plan_creates_only_what_is_missing_and_says_why_it_skips(tmp_path):
    f = folders(tmp_path)
    legacy = imp.legacy_projects(make_state(tmp_path, f))
    existing = [{'name': 'something else', 'primary_path': f['alpha'].upper(), 'folders': []}, {'name': 'beta', 'primary_path': None, 'folders': []}]
    actions = {a['name']: a for a in imp.plan(legacy, existing)}
    assert actions['Alpha']['action'] == 'skip' and 'owns that folder' in actions['Alpha']['reason']
    assert actions['Beta']['action'] == 'skip' and 'name exists' in actions['Beta']['reason']
    assert actions['Gamma']['action'] == 'skip' and actions['Delta']['action'] == 'skip'
    fresh = {a['name']: a for a in imp.plan(legacy, [])}
    assert fresh['Alpha']['action'] == 'create' and fresh['Beta']['action'] == 'create'


def test_execute_calls_only_projects_create_with_the_folder_and_survives_one_failure(tmp_path):
    f = folders(tmp_path)
    calls = []

    class Rpc:
        def call(self, method, params=None):
            calls.append((method, params))
            if params['name'] == 'Beta':
                raise RuntimeError('refused')
            return {}

    actions = imp.plan(imp.legacy_projects(make_state(tmp_path, f)), [])
    done = dict(imp.execute(actions, Rpc()))
    assert done['Alpha'] == 'created' and done['Beta'].startswith('failed')
    assert {m for m, _ in calls} == {'projects.create'}
    assert calls[0][1] == {'name': 'Alpha', 'folders': [f['alpha']], 'primary_path': f['alpha'], 'color': '#7cb9ff'}
    assert all('session' not in json.dumps(p).lower() for _, p in calls)


def test_the_tool_has_no_way_to_move_conversations_or_change_grants():
    source = (Path(__file__).resolve().parents[2] / 'src/hermes_projects_import.py').read_text(encoding='utf-8')
    code = source.split('"""', 2)[2]
    for forbidden in ('session.workspace.move', 'session.cwd', 'set_write_roots', 'write_roots', 'projects.delete', 'projects.archive'):
        assert forbidden not in code

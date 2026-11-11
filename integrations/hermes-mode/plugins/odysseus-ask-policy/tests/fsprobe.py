"""P0-2 probe: load the plugin into a disposable Hermes home and ask every public write predicate about a table of paths.
Nothing is written anywhere. Run by test_integration.py; prints one JSON document after @@RESULTS@@.
env: ODY_HOME (disposable home), ODY_AGENT, ODY_SITE, ODY_WORK (the safe root), ODY_MODE = normal | shape_broken
"""
import json
import os
import sys

HOME, WORK = os.environ['ODY_HOME'], os.environ['ODY_WORK']
os.environ['HERMES_HOME'] = HOME
os.environ['HERMES_WRITE_SAFE_ROOT'] = WORK
sys.path.insert(0, os.environ['ODY_AGENT'])
for extra in filter(None, os.environ.get('ODY_SITE', '').split(os.pathsep)):
    sys.path.append(extra)

from agent import file_safety as fs                                       # noqa: E402

if os.environ.get('ODY_MODE') == 'shape_broken':
    fs._classify_write_denial = lambda path: None                         # an upstream refactor: no `entry` parameter

from hermes_cli import plugins as plugin_api                              # noqa: E402

plugin_api.discover_plugins()
from tools import file_operations                                         # noqa: E402

try:
    from agent import acp_client
except Exception as exc:                                                  # noqa: BLE001
    acp_client = None
    acp_error = repr(exc)[:200]

SECRET = os.path.join(WORK, 'secrets', 'prod.env')
SECRET_DIR_CHILD = os.path.join(WORK, 'private', 'deep', 'notes.txt')
PLAIN = os.path.join(WORK, 'src', 'main.py')
OUTSIDE = os.path.join(os.path.dirname(WORK), 'elsewhere', 'x.txt')
CREDENTIAL = os.path.join(HOME, '.env')
PATHS = {'plain': PLAIN, 'never-allowed file': SECRET, 'under never-allowed folder': SECRET_DIR_CHILD, 'outside safe root': OUTSIDE, 'agent credential': CREDENTIAL}


def table():
    rows = {}
    for label, path in PATHS.items():
        row = {
            'classifier': fs._classify_write_denial(path),
            'classifier_entry': fs._classify_write_denial(path, entry=True),
            'is_write_denied': fs.is_write_denied(path),
            'get_write_denied_error': bool(fs.get_write_denied_error(path)),
            'get_write_denied_error_entry': bool(fs.get_write_denied_error(path, verb='Delete', entry=True)),
            'file_operations_alias': bool(file_operations.get_write_denied_error(path)),
        }
        if acp_client is not None:
            row['acp_client_alias'] = bool(acp_client.get_write_denied_error(path))
        rows[label] = row
    return rows


def write_state(blocked, raw=None):
    vault = os.path.join(HOME, 'vault')
    os.makedirs(vault, exist_ok=True)
    target = os.path.join(vault, 'webui-trusted-workspaces.json')
    if raw is not None:
        open(target, 'w').write(raw)
    elif blocked is None:
        if os.path.exists(target):
            os.remove(target)
    else:
        open(target, 'w').write(json.dumps({'version': 1, 'always': [], 'blocked': [{'path': p, 'added': 'x'} for p in blocked]}))


out = {'mode': os.environ.get('ODY_MODE', 'normal'), 'acp_client': 'imported' if acp_client is not None else acp_error}
out['installed'] = getattr(fs._classify_write_denial, '_ody_wrapped', False)
if os.environ.get('ODY_MODE') == 'shape_broken':
    status_file = os.path.join(HOME, 'odysseus', 'ask-policy.status.json')
    out['status'] = json.load(open(status_file)) if os.path.exists(status_file) else None
    print('\n@@RESULTS@@' + json.dumps(out))
    sys.exit(0)
out['no_state'] = table()
write_state([SECRET, os.path.dirname(SECRET_DIR_CHILD).rsplit(os.sep, 1)[0]])      # a file and a folder (WORK/private)
out['with_never_allow'] = table()
write_state([], raw='{ this is not json')
out['corrupt_state'] = table()
write_state([])
out['empty_blocked'] = table()
write_state(None)
out['state_removed'] = table()
status_path = os.path.join(HOME, 'odysseus', 'ask-policy.status.json')
out['status'] = json.load(open(status_path)) if os.path.exists(status_path) else None
print('\n@@RESULTS@@' + json.dumps(out))

"""Bring the legacy WebUI projects into the Hermes Webapp's own project store (operator tool, dry run by default).

The Webapp keeps first-class projects in the gateway home's ``projects.db`` and groups conversations by FOLDER: a conversation belongs to the project that
owns its working folder. The legacy WebUI instead tagged conversations with a project id. Every legacy project here has its conversations in one folder,
so creating the matching Webapp project (name, colour, that folder) is enough for the Webapp's sidebar to group them; no conversation is moved or edited.

What this never does: move or rewrite a conversation, change a working folder, touch write grants, or run inside the web app. It writes only through the
gateway's own ``projects.create`` method, with the gateway's session token read from the local file, from a command the operator starts.
"""
import asyncio
import collections
import itertools
import json
import os
import re
from pathlib import Path

COLOUR = re.compile(r'#[0-9a-fA-F]{6}')


def _key(path):
    return os.path.normcase(os.path.normpath(str(path))).rstrip('\\/')


def _read(path, default):
    try:
        return json.loads(Path(path).read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return default


def legacy_projects(webui_state):
    """[{name, colour, folder, conversations}] from the WebUI registry; folder is the one existing directory most of the project's conversations live in."""
    state = Path(webui_state)
    registry = _read(state / 'projects.json', [])
    index = _read(state / 'sessions' / '_index.json', [])
    if isinstance(index, dict):
        index = index.get('sessions') or []
    folders = collections.defaultdict(collections.Counter)
    for row in index:
        if isinstance(row, dict) and row.get('project_id') and row.get('workspace'):
            folders[row['project_id']][str(row['workspace'])] += 1
    found = []
    for entry in registry if isinstance(registry, list) else []:
        if not isinstance(entry, dict) or not entry.get('name'):
            continue
        counts = folders.get(entry.get('project_id'), collections.Counter())
        usable = [(n, f) for f, n in counts.most_common() if os.path.isdir(f)]
        found.append({'name': str(entry['name']).strip(), 'colour': entry.get('color') if COLOUR.fullmatch(str(entry.get('color') or '')) else None,
                      'folder': usable[0][1] if usable else None, 'conversations': usable[0][0] if usable else 0, 'folders_seen': list(counts)})
    return found


def plan(legacy, existing):
    """One action per legacy project: create, or skip with the reason. ``existing`` is the gateway's project list (archived ones included)."""
    paths = {_key(p['primary_path']) for p in existing if p.get('primary_path')} | {_key(f['path']) for p in existing for f in p.get('folders') or []}
    names = {str(p.get('name', '')).casefold() for p in existing}
    actions = []
    for item in legacy:
        if not item['folder']:
            actions.append({**item, 'action': 'skip', 'reason': 'no existing folder is known for its conversations'})
        elif _key(item['folder']) in paths:
            actions.append({**item, 'action': 'skip', 'reason': 'a Hermes project already owns that folder'})
        elif item['name'].casefold() in names:
            actions.append({**item, 'action': 'skip', 'reason': 'a Hermes project with that name exists'})
        else:
            actions.append({**item, 'action': 'create', 'reason': ''})
    return actions


class GatewayRpc:
    """Minimal JSON-RPC client for the gateway's own websocket (one connection per command)."""

    def __init__(self, url, token):
        self.url, self.token = url.rstrip('/'), token

    async def run(self, calls):
        import websockets
        host = self.url.split('://', 1)[1]
        ids = itertools.count(1)
        results = []
        async with websockets.connect(f'ws://{host}/api/ws?token={self.token}', additional_headers={'Origin': f'http://{host}'}, open_timeout=15, max_size=2 ** 24) as ws:
            await asyncio.wait_for(ws.recv(), 10)                      # gateway.ready
            for method, params in calls:
                rid = f'p{next(ids)}'
                await ws.send(json.dumps({'jsonrpc': '2.0', 'id': rid, 'method': method, 'params': params}))
                while True:
                    message = json.loads(await asyncio.wait_for(ws.recv(), 30))
                    if message.get('id') == rid:
                        if 'error' in message:
                            raise RuntimeError(f"{method}: {message['error'].get('message')}")
                        results.append(message.get('result'))
                        break
        return results

    def call(self, method, params=None):
        return asyncio.run(self.run([(method, params or {})]))[0]


def token_for(home):
    return (Path(home) / 'webapp' / 'session-token').read_text(encoding='utf-8').strip()


def execute(actions, rpc):
    """Create the planned projects through ``projects.create``. Returns [(name, 'created'|'failed: ...')]."""
    done = []
    for item in actions:
        if item['action'] != 'create':
            continue
        params = {'name': item['name'], 'folders': [item['folder']], 'primary_path': item['folder']}
        if item.get('colour'):
            params['color'] = item['colour']
        try:
            rpc.call('projects.create', params)
            done.append((item['name'], 'created'))
        except Exception as exc:                                       # one failure must not stop the rest
            done.append((item['name'], f'failed: {exc}'))
    return done

"""T4: the essential security/integration matrix, driven through the REAL Webapp WebSocket (/api/ws, JSON-RPC) of the LAB gateway.
Deterministic: a scripted stub model, approval requests answered immediately by this client (deny unless a scenario says once),
and a short test-only approval timeout (8 s) in the lab config.  Records for each scenario:
  asked    - did the gateway raise an approval request (and with which choices)?
  result   - the tool result text the model received
  effect   - did the dangerous effect happen on disk?
Usage: python t4_webapp_essentials.py LABEL [scenario ...]   (results appended to results/T4-LABEL.json)
"""
import asyncio
import itertools
import json
import os
import sys
import time
import uuid

import websockets

LAB = r'F:\Odysseus-Backups\desktop-lab'
TOKEN = open(LAB + r'\home\webapp\session-token').read().strip()
URL = f'ws://127.0.0.1:19120/api/ws?token={TOKEN}'
STUB_LOG = LAB + r'\logs\stub-llm.jsonl'
OUT, SAFE, HOME = LAB + r'\work\outside', LAB + r'\work\safe', LAB + r'\home'

SCEN = {  # name -> (text, effect predicate, answer)
    'S1_write_outside_root': ('T05', lambda: os.path.exists(OUT + r'\t05.txt'), 'deny'),
    'S2_write_never_allow_in_root': ('T07', lambda: os.path.exists(SAFE + r'\secrets\new-secret.env'), 'deny'),
    'S3_write_plugin_dir_in_wide_root': ('T17', lambda: os.path.exists(HOME + r'\plugins\evil\plugin.yaml'), 'deny'),
    'S4_write_webapp_dir_in_wide_root': ('T18', lambda: os.path.exists(HOME + r'\webapp\planted.txt'), 'deny'),
    'S5_terminal_powershell_write': ('T02', lambda: os.path.exists(OUT + r'\t02.txt'), 'deny'),
    'S6_terminal_git_push': ('T03', lambda: False, 'deny'),
    'S7_execute_code_write_outside': ('T09', lambda: os.path.exists(OUT + r'\t09.txt'), 'deny'),
    'S8_mcp_tool_write_outside': ('T11', lambda: os.path.exists(OUT + r'\t11.txt'), 'deny'),
    'S9_subagent_delegate': ('T10', lambda: False, 'deny'),
    'S10_terminal_read_only_git_status': ('T15', lambda: False, 'deny'),
}


def cleanup():
    for p in (OUT + r'\t02.txt', OUT + r'\t05.txt', OUT + r'\t09.txt', OUT + r'\t11.txt', OUT + r'\t01.txt', HOME + r'\webapp\planted.txt',
              HOME + r'\plugins\evil\plugin.yaml', SAFE + r'\secrets\new-secret.env'):
        try:
            os.remove(p)
        except OSError:
            pass


class Client:
    def __init__(self):
        self.ids = itertools.count(1)
        self.waiters = {}
        self.events = asyncio.Queue()

    async def connect(self):
        self.ws = await websockets.connect(URL, additional_headers={'Origin': 'http://127.0.0.1:19120'}, max_size=2 ** 24, open_timeout=15)
        self.reader = asyncio.create_task(self._read())
        await asyncio.wait_for(self.events.get(), 10)          # gateway.ready

    async def _read(self):
        try:
            async for raw in self.ws:
                msg = json.loads(raw)
                if 'id' in msg and msg['id'] in self.waiters:
                    self.waiters.pop(msg['id']).set_result(msg)
                else:
                    await self.events.put(msg)
        except Exception:
            pass

    async def rpc(self, method, params, timeout=30):
        rid = f'r{next(self.ids)}'
        fut = asyncio.get_running_loop().create_future()
        self.waiters[rid] = fut
        await self.ws.send(json.dumps({'jsonrpc': '2.0', 'id': rid, 'method': method, 'params': params}))
        return await asyncio.wait_for(fut, timeout)


def find_request(msg):
    params = msg.get('params') or {}
    if params.get('type') == 'approval.request' and isinstance(params.get('payload'), dict):
        payload = dict(params['payload'])
        payload.setdefault('choices', ['once', 'session', 'always', 'deny'])
        payload['request_id'] = payload.get('request_id') or payload.get('prompt_id') or payload.get('id')
        return payload
    payload = params.get('payload') if isinstance(params.get('payload'), dict) else params
    if isinstance(payload, dict) and payload.get('choices') and (payload.get('request_id') or payload.get('id')):
        return payload
    return None


async def run_one(client, name, text, effect, answer, hard=45):
    cleanup()
    mark = os.path.getsize(STUB_LOG) if os.path.exists(STUB_LOG) else 0
    t0 = time.time()
    created = await client.rpc('session.create', {})
    sid = (created.get('result') or {}).get('session_id') or (created.get('result') or {}).get('id')
    if not sid:
        return {'scenario': name, 'error': f'session.create failed: {str(created)[:200]}'}
    sub = await client.rpc('prompt.submit', {'session_id': sid, 'text': f'SCENARIO:{text} please run it', 'submission_id': uuid.uuid4().hex})
    asked, kinds = [], []
    while time.time() - t0 < hard:
        try:
            msg = await asyncio.wait_for(client.events.get(), 1.0)
        except asyncio.TimeoutError:
            if os.path.exists(STUB_LOG):
                with open(STUB_LOG, encoding='utf-8') as handle:
                    handle.seek(mark)
                    lines = [json.loads(x) for x in handle if x.strip()]
                if any(x.get('reply') == 'final' and x.get('scenario') == text for x in lines):
                    break
            continue
        params = msg.get('params') or {}
        kinds.append(params.get('type') or msg.get('method'))
        request = find_request(msg)
        if request:
            asked.append({'choices': request.get('choices'), 'description': str(request.get('description') or request.get('command') or '')[:110]})
            rid = request.get('request_id') or request.get('id')
            reply = await client.rpc('approval.respond', {'session_id': sid, 'prompt_id': rid, 'execution_generation': request.get('execution_generation', 1), 'choice': answer}, timeout=10)
            asked[-1]['answer_reply'] = str(reply.get('result') or reply.get('error'))[:70]
    lines = []
    if os.path.exists(STUB_LOG):
        with open(STUB_LOG, encoding='utf-8') as handle:
            handle.seek(mark)
            lines = [json.loads(x) for x in handle if x.strip()]
    results = [x['result'] for x in lines if x.get('result') and x.get('scenario') == text]
    return {'scenario': name, 'asked': asked, 'asked_n': len(asked), 'effect_happened': bool(effect()), 'result': (results[-1] if results else '')[:260],
            'seconds': round(time.time() - t0, 1), 'event_types': sorted({k for k in kinds if k})[:14], 'submit': str(sub.get('result') or sub.get('error'))[:80]}


async def main():
    label, names = sys.argv[1], sys.argv[2:] or list(SCEN)
    overrides = {}
    for item in names:
        if ':' in item:
            base, _, ans = item.partition(':')
            overrides[base] = ans
    names = [n.split(':')[0] for n in names]
    client = Client()
    await client.connect()
    path = LAB + rf'\results\T4-{label}.json'
    done = {r['scenario']: r for r in (json.load(open(path)) if os.path.exists(path) else [])}
    for name in names:
        text, effect, answer = SCEN[name]
        answer = overrides.get(name, answer)
        try:
            row = await run_one(client, name, text, effect, answer)
        except Exception as exc:
            row = {'scenario': name, 'error': f'{type(exc).__name__}: {str(exc)[:160]}'}
        row['answered'] = answer
        done[name + (':' + answer if name in overrides else '')] = row
        json.dump(list(done.values()), open(path, 'w'), indent=1)
        mark = '' if 'error' not in row else ' ERROR'
        print(f"{name:36} asked={row.get('asked_n')} effect={row.get('effect_happened')} {row.get('seconds')}s{mark}\n    result: {row.get('result', row.get('error'))[:200]}", flush=True)


asyncio.run(main())

"""Lab only: live scoped-logout probe against the disposable preview (real Odysseus app + real official Webapp gateway).

Needs results/grants-live.json (grantA/grantB/cookieB), results/hermes-token.txt (the lab Webapp's bearer token) and results/tabs.json state from the browser run.
Run with the venv that has `websockets`. Secrets are never printed."""
import asyncio
import http.client
import json
import sys
import time

import websockets
from websockets.exceptions import ConnectionClosed, InvalidStatus

P, GW = 19800, 19120
O, H = f'localhost:{P}', f'hermes.localhost:{P}'
LAB = r'F:\Odysseus-Backups\desktop-lab\results'
g = json.load(open(LAB + r'\grants-live.json'))
token = open(LAB + r'\hermes-token.txt').read().strip()
rows = []


def record(name, got, expect=None):
    ok = '' if expect is None else ('PASS' if expect in str(got) else 'FAIL')
    rows.append({'check': name, 'got': str(got), 'expect': expect, 'ok': ok})
    print(f'{name:70} -> {got}' + (f'   {ok}' if ok else ''))


def call(host, path, method='GET', headers=None, port=P):
    c = http.client.HTTPConnection('127.0.0.1', port, timeout=15)
    c.putrequest(method, path, skip_host=True)
    c.putheader('Host', host)
    for k, v in (headers or {}).items():
        c.putheader(k, v)
    if method == 'POST':
        c.putheader('Content-Length', '0')
    c.endheaders()
    r = c.getresponse()
    body = r.read()
    out = (r.status, body, {k.lower(): v for k, v in r.getheaders()})
    c.close()
    return out


def via(grant, path='/api/health', bearer=False, method='GET', extra=None):
    h = {'Cookie': f'odysseus_hermes_grant={grant}'} if grant else {}
    if bearer:
        h['Authorization'] = f'Bearer {token}'
    h.update(extra or {})
    s, b, d = call(H, path, method, h)
    return f'{s} ' + ('GATE-DENIED' if d.get('x-odysseus-grant') == 'required' else 'passed-to-gateway')


def ticket(grant):
    s, b, d = call(H, '/api/webapp/socket-ticket?socket=gateway', 'POST',
                   {'Cookie': f'odysseus_hermes_grant={grant}', 'Authorization': f'Bearer {token}', 'Origin': f'http://{H}'})
    return json.loads(b)['ticket'] if s == 200 else None


async def ws_attempt(grant, tk):
    try:
        async with websockets.connect(f'ws://{H}/api/ws', host='127.0.0.1', port=P, origin=f'http://{H}', max_size=None,
                                      additional_headers={'Cookie': f'odysseus_hermes_grant={grant}'},
                                      subprotocols=['hermes-gateway-v1', f'hermes-gateway-ticket.{tk}']) as ws:
            return f'101 {ws.subprotocol}'
    except InvalidStatus as exc:
        body = (exc.response.body or b'')[:60]
        return f'{exc.response.status_code} ' + ('GATE-DENIED' if b'Open Hermes from Odysseus' in body else 'refused-by-gateway')
    except Exception as exc:
        return type(exc).__name__


async def main():
    print('== 1. Revoked session A (logged out of Odysseus in the browser run)')
    record('A  HTTP public route', via(g['grantA']), '401 GATE-DENIED')
    record('A  HTTP protected route with a VALID Hermes bearer token', via(g['grantA'], '/api/profiles', bearer=True), '401 GATE-DENIED')
    record('A  POST with a valid bearer', via(g['grantA'], '/api/ui-preferences', bearer=True, method='POST', extra={'Origin': f'http://{H}'}), '401 GATE-DENIED')
    record('A  cannot mint a WebSocket ticket', via(g['grantA'], '/api/webapp/socket-ticket?socket=gateway', bearer=True, method='POST', extra={'Origin': f'http://{H}'}), '401 GATE-DENIED')
    for i in range(3):
        record(f'A  WebSocket reconnect attempt {i + 1} (junk ticket)', await ws_attempt(g['grantA'], 'A' * 43), '401 GATE-DENIED')
    record('(control) B  WebSocket with a junk ticket is refused by the GATEWAY, not the gate', await ws_attempt(g['grantB'], 'A' * 43), 'refused-by-gateway')

    print('== 2. Browser B: same Odysseus user, its own session and grant (never logged out yet)')
    record('B  HTTP public route', via(g['grantB']), '200 passed-to-gateway')
    record('B  protected route with the Hermes bearer', via(g['grantB'], '/api/profiles', bearer=True), '200 passed-to-gateway')
    record('B  protected route WITHOUT the Hermes bearer: gateway auth still decides', via(g['grantB'], '/api/profiles'), '401 passed-to-gateway')
    record('no grant at all, valid bearer', via('', '/api/profiles', bearer=True), '401 GATE-DENIED')

    print('== 3. Live gateway WebSockets of B, then B logs out of Odysseus')
    sockets = []
    for i in range(2):
        tk = ticket(g['grantB'])
        ws = await websockets.connect(f'ws://{H}/api/ws', host='127.0.0.1', port=P, origin=f'http://{H}', max_size=None,
                                      additional_headers={'Cookie': f'odysseus_hermes_grant={g["grantB"]}'},
                                      subprotocols=['hermes-gateway-v1', f'hermes-gateway-ticket.{tk}'])
        sockets.append(ws)
    record('two real gateway sockets open for B', [ws.subprotocol for ws in sockets], 'hermes-gateway-v1')
    await asyncio.sleep(1)
    record('sockets open before logout', all(ws.close_code is None for ws in sockets), 'True')
    started = time.monotonic()
    s, b, d = await asyncio.to_thread(call, O, '/api/auth/logout', 'POST', {'Origin': f'http://{O}', 'Cookie': f'odysseus_session={g["cookieB"]}'})
    record("B  Odysseus logout", s, '200')
    closed = await asyncio.gather(*[asyncio.wait_for(ws.wait_closed(), 5) for ws in sockets], return_exceptions=True)
    elapsed = time.monotonic() - started
    record('B  both live sockets closed', [(ws.close_code, ws.close_reason) for ws in sockets], '4401')
    record('B  time from logout to both closed (seconds)', round(elapsed, 2))
    rows.append({'check': 'B close latency under 2 s', 'got': str(round(elapsed, 2)), 'expect': '<2', 'ok': 'PASS' if elapsed < 2 else 'FAIL'})
    record('B  HTTP right after logout', via(g['grantB'], '/api/profiles', bearer=True), '401 GATE-DENIED')
    for i in range(2):
        record(f'B  WebSocket reconnect attempt {i + 1}', await ws_attempt(g['grantB'], 'A' * 43), '401 GATE-DENIED')

    print('== 4. What proxy-side logout does NOT do (stated limitation, measured)')
    s, b, d = call(f'127.0.0.1:{GW}', '/api/profiles', 'GET', {'Authorization': f'Bearer {token}'}, port=GW)
    record('DIRECT gateway port with the same Hermes token (no proxy, no grant)', s, '200')
    s, b, d = call(f'127.0.0.1:{GW}', '/api/health', 'GET', port=GW)
    record('DIRECT gateway health', s, '200')

    json.dump(rows, open(LAB + r'\grants-live-probe.json', 'w'), indent=1)
    print('PASS', sum(r['ok'] == 'PASS' for r in rows), 'FAIL', sum(r['ok'] == 'FAIL' for r in rows), 'info', sum(r['ok'] == '' for r in rows))

asyncio.run(main())

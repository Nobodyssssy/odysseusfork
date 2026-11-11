"""T1: Webapp authentication, HTTP Host/Origin, and WebSocket Origin/Host tests against the LAB gateway only (port 19120)."""
import asyncio
import json
import os
import sys

import httpx
import websockets

LAB = r'F:\Odysseus-Backups\desktop-lab'
BASE = 'http://127.0.0.1:19120'
TOKEN = open(LAB + r'\home\webapp\session-token').read().strip()
rows = []


def rec(group, name, expect, got, note=''):
    ok = 'PASS' if str(got) in expect.split('|') else 'FAIL'
    rows.append({'group': group, 'test': name, 'expected': expect, 'got': str(got), 'verdict': ok, 'note': note})
    print(f'{ok:4} {group:6} {name:62} expect {expect:12} got {got} {note}')


def http(method, path, headers=None, **kw):
    try:
        r = httpx.request(method, BASE + path, headers=headers or {}, timeout=15, follow_redirects=False, **kw)
        return r.status_code, r.text[:200]
    except Exception as exc:
        return type(exc).__name__, ''


H = {'X-Hermes-Session-Token': TOKEN}
# ---- A: unauthenticated REST must be refused
for method, path, body in [('GET', '/api/sessions', None), ('GET', '/api/config', None), ('GET', '/api/fs/list?path=C:/', None), ('GET', '/api/files?path=.', None),
                           ('GET', '/api/git/status?path=.', None), ('GET', '/api/mcp/servers', None), ('GET', '/api/profiles', None),
                           ('POST', '/api/fs/write-text', {}), ('DELETE', '/api/files', {}), ('POST', '/api/git/review/commit', {}),
                           ('POST', '/api/git/review/push', {}), ('POST', '/api/mcp/servers', {}), ('PUT', '/api/tools/terminal/backend', {}),
                           ('POST', '/api/files/upload', {})]:
    code, _ = http(method, path, json=body) if body is not None else http(method, path)
    rec('A-auth', f'no credential: {method} {path}', '401|403', code)
# ---- B: wrong token
code, _ = http('GET', '/api/sessions', {'X-Hermes-Session-Token': 'x' * 43})
rec('A-auth', 'wrong token: GET /api/sessions', '401|403', code)
# ---- C: right token on loopback host works
code, _ = http('GET', '/api/sessions', H)
rec('B-ok', 'valid token + Host 127.0.0.1:19120: GET /api/sessions', '200', code)
code, _ = http('GET', '/api/sessions', {**H, 'Host': 'localhost:19120'})
rec('B-ok', 'valid token + Host localhost:19120', '200', code)
# ---- D: HTTP rebinding / host variants (valid token, since a rebinding page cannot know it, but the Host rule must hold anyway)
for host, expect, note in [('evil.test:19120', '400|403|421', 'DNS rebinding'), ('hermes.localhost:19120', '400|403|421|200', 'intended fused host: needs public_url'),
                           ('hermes.localhost:7000', '400|403|421|200', 'fused host as the browser sends it'), ('127.0.0.1.evil.test:19120', '400|403|421', 'suffix trick'),
                           ('evil.test', '400|403|421', 'no port'), ('', '400|403|421|200', 'empty host')]:
    code, _ = http('GET', '/api/sessions', {**H, 'Host': host}) if host else http('GET', '/api/sessions', H)
    rec('C-host', f'HTTP valid token + Host {host!r}', expect, code, note)
    code, _ = http('GET', '/api/sessions', {'Host': host}) if host else (None, None)
    if host:
        rec('C-host', f'HTTP NO token   + Host {host!r}', '400|401|403|421', code, note)
# ---- E: HTTP Origin behaviour
for origin in ('http://evil.example', 'null', 'http://127.0.0.1:19120', 'http://localhost:19120', 'http://hermes.localhost:7000'):
    code, _ = http('GET', '/api/sessions', {**H, 'Origin': origin})
    rec('D-orig', f'HTTP GET valid token + Origin {origin}', '200|400|403', code)
    code, _ = http('POST', '/api/fs/write-text', {**H, 'Origin': origin}, json={})
    rec('D-orig', f'HTTP POST (empty body) valid token + Origin {origin}', '400|403|422', code, 'a 400/422 means it reached the handler')
# CORS preflight from a hostile origin must not grant access
try:
    r = httpx.options(BASE + '/api/sessions', headers={'Origin': 'http://evil.example', 'Access-Control-Request-Method': 'GET'}, timeout=10)
    allow = r.headers.get('access-control-allow-origin')
except Exception as exc:
    allow = type(exc).__name__
rec('D-orig', 'CORS preflight from http://evil.example: allow-origin header', 'None', allow)


# ---- F: WebSockets
async def ws_try(path, origin=None, host=None, token=None, query=True):
    url = f'ws://127.0.0.1:19120{path}'
    if token is not None and query:
        url += ('&' if '?' in url else '?') + f'token={token}'
    headers = {}
    if origin is not None:
        headers['Origin'] = origin
    if host is not None:
        headers['Host'] = host
    if token is not None and not query:
        headers['X-Hermes-Session-Token'] = token
    try:
        async with websockets.connect(url, additional_headers=headers, open_timeout=10, close_timeout=2, max_size=2 ** 20) as sock:
            try:
                msg = await asyncio.wait_for(sock.recv(), 3)
                return 'OPEN', str(msg)[:80]
            except asyncio.TimeoutError:
                return 'OPEN', 'quiet'
            except websockets.ConnectionClosed as closed:
                return f'closed {closed.rcvd.code if closed.rcvd else "?"}', (closed.rcvd.reason if closed.rcvd else '')[:60]
    except websockets.InvalidStatus as status:
        return f'HTTP {status.response.status_code}', ''
    except Exception as exc:
        return type(exc).__name__, str(exc)[:60]


async def ws_tests():
    for path in ('/api/ws', '/api/events', '/api/pub', '/api/pty', '/api/host-terminal', '/api/console'):
        for label, kw, expect in [
            ('no token, no origin', dict(), 'refused'),
            ('no token, Origin evil', dict(origin='http://evil.example'), 'refused'),
            ('TOKEN + Origin evil', dict(origin='http://evil.example', token=TOKEN), 'refused'),
            ('TOKEN + Origin null', dict(origin='null', token=TOKEN), 'refused'),
            ('TOKEN + rebinding Host+Origin evil.test', dict(origin='http://evil.test:19120', host='evil.test:19120', token=TOKEN), 'refused'),
            ('TOKEN + fused origin hermes.localhost:7000', dict(origin='http://hermes.localhost:7000', token=TOKEN), 'info'),
            ('TOKEN + same-origin 127.0.0.1:19120', dict(origin='http://127.0.0.1:19120', token=TOKEN), 'info'),
            ('TOKEN, no Origin header (non-browser client)', dict(token=TOKEN), 'info'),
        ]:
            result, detail = await ws_try(path, **kw)
            if expect == 'refused':
                good = result.startswith(('closed 44', 'closed 10', 'HTTP 40', 'HTTP 41', 'InvalidStatus', 'InvalidHandshake')) or result == 'ConnectionClosedError'
                rows.append({'group': 'E-ws', 'test': f'{path} {label}', 'expected': 'refused', 'got': f'{result} {detail}', 'verdict': 'PASS' if good else 'FAIL', 'note': ''})
                print(f'{"PASS" if good else "FAIL":4} E-ws   {path:20} {label:50} refused? got {result} {detail}')
            else:
                rows.append({'group': 'E-ws', 'test': f'{path} {label}', 'expected': 'info', 'got': f'{result} {detail}', 'verdict': 'INFO', 'note': ''})
                print(f'INFO E-ws   {path:20} {label:50} got {result} {detail}')


asyncio.run(ws_tests())
json.dump(rows, open(LAB + r'\results\T1-webapp-security.json', 'w'), indent=1)
fails = [r for r in rows if r['verdict'] == 'FAIL']
print(f'\nSUMMARY: {len(rows)} checks, {sum(r["verdict"] == "PASS" for r in rows)} pass, {len(fails)} FAIL, {sum(r["verdict"] == "INFO" for r in rows)} info')
for r in fails:
    print('  FAIL:', r['group'], r['test'], '->', r['got'])

"""Lab only: probe the Webapp's sign-in gate (webapp.public_url + basic-auth provider) THROUGH the Odysseus proxy.
Uses the lab gate credential from results/gate-secret.json (never printed). Run with the Odysseus preview venv."""
import asyncio
import http.client
import json
import socket
import sys

import websockets

P = 19800
H = f'hermes.localhost:{P}'
GW = 19120
secret = json.load(open(r'F:\Odysseus-Backups\desktop-lab\results\gate-secret.json'))
rows = []


def record(name, got, expect=None):
    ok = '' if expect is None else ('  PASS' if str(got).startswith(str(expect)) or expect in str(got) else '  FAIL')
    rows.append({'check': name, 'got': str(got), 'expect': expect, 'ok': ok.strip()})
    print(f'{name:62} -> {got}{ok}')


def http_call(host, path, method='GET', headers=None, body=None, cookies=None, port=P):
    c = http.client.HTTPConnection('127.0.0.1', port, timeout=15)
    c.putrequest(method, path, skip_host=True)
    c.putheader('Host', host)
    if cookies:
        c.putheader('Cookie', '; '.join(f'{k}={v}' for k, v in cookies.items()))
    for k, v in (headers or {}).items():
        c.putheader(k, v)
    data = json.dumps(body).encode() if body is not None else b''
    if body is not None:
        c.putheader('Content-Type', 'application/json')
    if method == 'POST':
        c.putheader('Content-Length', str(len(data)))
    c.endheaders(data if data else None)
    r = c.getresponse()
    out = r.read()
    result = (r.status, out, [(k.lower(), v) for k, v in r.getheaders()])
    c.close()
    return result


def cookie_jar(headers):
    jar, flags = {}, {}
    for k, v in headers:
        if k == 'set-cookie':
            pair, *attrs = [x.strip() for x in v.split(';')]
            name, _, value = pair.partition('=')
            jar[name] = value
            flags[name] = sorted({a.split('=')[0].lower() for a in attrs} & {'httponly', 'secure', 'samesite', 'max-age', 'expires', 'domain', 'path'})
            if 'samesite' in flags[name]:
                flags[name].append(next(a for a in attrs if a.lower().startswith('samesite')).lower())
    return jar, flags


ORIGIN = {'Origin': f'http://{H}'}
JSON_ORIGIN = {**ORIGIN}

print('== 1. Unauthenticated through the proxy (gate on)')
s, b, hd = http_call(H, '/api/health')
record('GET /api/health (public)', f'{s} auth_required={json.loads(b).get("auth_required")}', '200 auth_required=True')
for path in ('/api/profiles', '/api/sessions', '/api/config', '/api/fs/read-text?path=x', '/api/git/status?path=x'):
    s, b, hd = http_call(H, path)
    record(f'GET {path.split("?")[0]} no credentials', s, '401')
s, b, hd = http_call(H, '/')
record('GET / (shell or login redirect)', f'{s} location={dict(hd).get("location", "-")}')
s, b, hd = http_call(H, '/login')
record('GET /login (server-rendered login page)', f'{s} bytes={len(b)}', '200')
s, b, hd = http_call(H, '/api/auth/providers')
provs = json.loads(b) if s == 200 else None
record('GET /api/auth/providers', f'{s} {json.dumps(provs)[:120]}', '200')
provider = None
if isinstance(provs, dict):
    items = provs.get('providers') or provs.get('items') or []
    provider = next((p['name'] for p in items if isinstance(p, dict) and p.get('supports_password')), None)
elif isinstance(provs, list) and provs:
    provider = next((p['name'] for p in provs if isinstance(p, dict) and p.get('supports_password')), None)
print('   password provider id:', provider)

print('== 2. Login through the proxy')
cases = [('wrong password', {'provider': provider, 'username': 'labgate', 'password': 'definitely-wrong'}, ORIGIN, '401'),
         ('wrong username', {'provider': provider, 'username': 'nobody', 'password': secret['password']}, ORIGIN, '401'),
         ('correct credentials, EVIL Origin (CSRF)', {'provider': provider, 'username': secret['username'], 'password': secret['password']}, {'Origin': 'http://evil.test'}, '403'),
         ('correct credentials, Origin: null', {'provider': provider, 'username': secret['username'], 'password': secret['password']}, {'Origin': 'null'}, '403'),
         ('correct credentials, cross-site fetch metadata', {'provider': provider, 'username': secret['username'], 'password': secret['password']}, {**ORIGIN, 'Sec-Fetch-Site': 'cross-site'}, '403')]
for name, body, hdr, expect in cases:
    s, b, hd = http_call(H, '/auth/password-login', 'POST', hdr, body)
    record(f'password-login: {name}', s, expect)
s, b, hd = http_call(H, '/auth/password-login', 'POST', ORIGIN, {'provider': provider, 'username': secret['username'], 'password': secret['password']})
jar, flags = cookie_jar(hd)
record('password-login: correct credentials, own Origin', f'{s} cookies={sorted(jar)}', '200')
for name, f in flags.items():
    print(f'   cookie {name[:34]:34} flags={f}')
record('every session cookie is HttpOnly', all('httponly' in f for f in flags.values()) if flags else False, 'True')
record('no cookie has a Domain attribute (host-only)', not any('domain' in f for f in flags.values()), 'True')

print('== 3. Authenticated HTTP')
s, b, hd = http_call(H, '/api/profiles', cookies=jar)
record('GET /api/profiles with login cookies', s, '200')
s, b, hd = http_call(H, '/api/profiles', cookies=jar, headers={'X-Forwarded-For': '9.9.9.9'})
record('  + X-Forwarded-For', s, '403')
s, b, hd = http_call(H, '/api/ui-preferences', 'POST', {'Origin': 'http://evil.test'}, {}, jar)
record('POST with login cookies, EVIL Origin', s, '403')
s, b, hd = http_call(f'evil.test:{P}', '/api/profiles', cookies=jar)
record('login cookies sent to forged Host evil.test (-> Odysseus)', f'{s} {b[:40]!r}', '401')
s, b, hd = http_call(H, '/api/auth/me', cookies=jar)
record('GET /api/auth/me', f'{s} {b[:80]!r}', '200')

print('== 4. WebSocket (gated flow: ticket minted with the login cookies)')


def mint():
    s, b, _ = http_call(H, '/api/webapp/socket-ticket?socket=gateway', 'POST', ORIGIN, None, jar)
    if s != 200:
        s2, b2, _ = http_call(H, '/api/auth/ws-ticket', 'POST', ORIGIN, None, jar)
        return json.loads(b2).get('ticket') if s2 == 200 else None, f'webapp:{s} dashboard:{s2}'
    return json.loads(b).get('ticket'), f'webapp:{s}'


ticket, how = mint()
record('mint a one-use socket ticket with login cookies', f'{how} got_ticket={bool(ticket)}', 'got_ticket=True')


def raw_ws(host, origin, tk, extra='', cookies=None):
    sock = socket.create_connection(('127.0.0.1', P), timeout=8)
    req = f'GET /api/ws HTTP/1.1\r\nHost: {host}\r\nUpgrade: websocket\r\nConnection: Upgrade\r\nSec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==\r\nSec-WebSocket-Version: 13\r\n'
    if tk:
        req += f'Sec-WebSocket-Protocol: hermes-gateway-v1, hermes-gateway-ticket.{tk}\r\n'
    if origin is not None:
        req += f'Origin: {origin}\r\n'
    if cookies:
        req += 'Cookie: ' + '; '.join(f'{k}={v}' for k, v in cookies.items()) + '\r\n'
    sock.sendall((req + extra + '\r\n').encode())
    line = sock.recv(400).decode('latin-1').split('\r\n')[0]
    sock.close()
    return line.replace('HTTP/1.1 ', '')


for name, o, h, x in [('evil Origin', 'http://evil.test', H, ''), ('Origin: null', 'null', H, ''), ('no Origin', None, H, ''),
                      ('cross-site fetch metadata', f'http://{H}', H, 'Sec-Fetch-Site: cross-site\r\n'),
                      ('X-Forwarded-For', f'http://{H}', H, 'X-Forwarded-For: 9.9.9.9\r\n'), ('forged Host', f'http://evil.test:{P}', f'evil.test:{P}', '')]:
    record(f'WS valid ticket + cookies, {name}', raw_ws(h, o, ticket, x, jar), '403' if 'forged' not in name else None)
record('WS no ticket, no cookies, own Origin', raw_ws(H, f'http://{H}', '', ''), '403')
record('WS forged ticket, no cookies', raw_ws(H, f'http://{H}', 'A' * 43, ''), '403')
record('WS the real ticket, legitimate request', raw_ws(H, f'http://{H}', ticket, '', jar), '101')
record('WS replay of that ticket', raw_ws(H, f'http://{H}', ticket, '', jar), '403')

print('== 5. Logout semantics (what each sign-out actually revokes)')
import subprocess, os
PYV = r'C:\Users\Protagonist\AppData\Local\hermes\installs\562e58e2ea9aca80\environments\6eff8cc60cb848909fbabec0b09c88c2\venv\Scripts\python.exe'


def hermes_sign_out():
    code = ("import json;from pathlib import Path;from hermes_cli.webapp_launcher import _control;"
            "r=_control(Path(r'F:\\Odysseus-Backups\\desktop-lab\\home'),'webapp-sign-out');print(json.dumps(r.get('result')))")
    env = {k: v for k, v in os.environ.items() if k not in ('PYTHONPATH', 'HERMES_HOME')}
    env['PYTHONPATH'] = r'F:\Odysseus-Backups\desktop-lab\src'
    out = subprocess.run([PYV, '-B', '-c', code], env=env, capture_output=True, text=True, timeout=60)
    return json.loads(out.stdout.strip().splitlines()[-1])


async def scenario():
    tk, _ = mint()
    async with websockets.connect(f'ws://hermes.localhost:{P}/api/ws', host='127.0.0.1', port=P, origin=f'http://{H}', max_size=None,
                                  subprotocols=['hermes-gateway-v1', f'hermes-gateway-ticket.{tk}']) as ws:
        record('live WebSocket negotiated protocol', ws.subprotocol, 'hermes-gateway-v1')
        s, b, hd = await asyncio.to_thread(http_call, H, '/auth/logout', 'POST', ORIGIN, {}, jar)
        record('A) POST /auth/logout (browser sign-out)', f'{s} location={dict(hd).get("location")}', '302')
        cleared, _f = cookie_jar(hd)
        record('   logout clears the browser cookies', all(v == '' or v == '""' for v in cleared.values()) if cleared else 'no Set-Cookie', 'True')
        await asyncio.sleep(2)
        record('   live socket after /auth/logout', 'still open' if ws.close_code is None else f'closed code={ws.close_code}')
        s, b, hd = await asyncio.to_thread(http_call, H, '/api/profiles', 'GET', None, None, jar)
        record('   COPIED cookies replayed after /auth/logout (stateless token)', s)
        out = await asyncio.to_thread(hermes_sign_out)
        record('B) webapp-sign-out (rotates the browser credential)', out, "'signed_out': True")
        try:
            await asyncio.wait_for(ws.wait_closed(), 10)
            record('   live socket after webapp-sign-out', f'closed code={ws.close_code}', 'closed')
        except asyncio.TimeoutError:
            record('   live socket after webapp-sign-out', 'STILL OPEN after 10s', 'closed')


asyncio.run(scenario())
s, b, hd = http_call(H, '/api/profiles', cookies=jar)
record('   copied cookies after webapp-sign-out', s, '401')
s, b, hd = http_call(H, '/api/webapp/socket-ticket?socket=gateway', 'POST', ORIGIN, None, jar)
record('   mint a ticket with copied cookies after webapp-sign-out', s, '401')

print('== 6. Direct gateway port, rebinding-style requests (gate on); authenticated with a FRESH login')
s, b, hd = http_call(H, '/auth/password-login', 'POST', ORIGIN, {'provider': provider, 'username': secret['username'], 'password': secret['password']})
fresh, _f = cookie_jar(hd)
record('fresh login after sign-out', s, '200')
for name, host, origin in [('Host evil.test', 'evil.test:19120', None), ('Host evil.test + Origin evil', 'evil.test', 'http://evil.test'),
                           ('Host 127.0.0.1.evil.test', '127.0.0.1.evil.test:19120', None),
                           ('Host hermes.localhost:19800 (the trusted public host)', 'hermes.localhost:19800', None),
                           ('Host 127.0.0.1:19120 (bound authority)', '127.0.0.1:19120', None)]:
    s, b, hd = http_call(host, '/api/profiles', port=GW, headers={'Origin': origin} if origin else None, cookies=fresh)
    record(f'direct :19120 WITH valid cookies, {name}', s)

print('== 7. Brute-force throttle (last, may lock the login for a short window)')
codes = []
for i in range(14):
    s, b, hd = http_call(H, '/auth/password-login', 'POST', ORIGIN, {'provider': provider, 'username': 'labgate', 'password': f'wrong-{i}'})
    codes.append(s)
record('14 wrong passwords in a row', codes, '401')
record('  -> throttled (429 seen)?', 429 in codes, 'True')
json.dump(rows, open(r'F:\Odysseus-Backups\desktop-lab\results\gate-probe.json', 'w'), indent=1)
import shutil; shutil.copy(r'F:\Odysseus-Backups\desktop-lab\results\gate-probe.json', r'F:\Odysseus\integration-backups\webapp-proxy\docs\evidence\preview\gate-probe.json')
print('PASS', sum(r['ok'] == 'PASS' for r in rows), 'FAIL', sum(r['ok'] == 'FAIL' for r in rows), 'info', sum(r['ok'] == '' for r in rows))

"""Lab only: scoped-logout behaviour across service restarts of the DISPOSABLE preview (Odysseus on 19800, gateway on 19120).

Never touches production: preview.py/lab.py only ever act on their own pid files and ports; the sessions file edited here is the preview's own."""
import http.client
import json
import os
import subprocess
import sys
import time

P, GW = 19800, 19120
O, H = f'localhost:{P}', f'hermes.localhost:{P}'
LAB = r'F:\Odysseus-Backups\desktop-lab'
PYV = r'C:\Users\Protagonist\AppData\Local\hermes\installs\562e58e2ea9aca80\environments\6eff8cc60cb848909fbabec0b09c88c2\venv\Scripts\python.exe'
FULL = LAB + r'\ody-full-venv\Scripts\python.exe'
env = {k: v for k, v in os.environ.items() if k not in ('PYTHONPATH', 'HERMES_HOME')}
sec = json.load(open(LAB + r'\odysseus-preview\lab-admin.secret'))
old = json.load(open(LAB + r'\results\grants-live.json'))
token = open(LAB + r'\results\hermes-token.txt').read().strip()
rows = []


def record(name, got, expect=None):
    ok = '' if expect is None else ('PASS' if expect in str(got) else 'FAIL')
    rows.append({'check': name, 'got': str(got), 'expect': expect, 'ok': ok})
    print(f'{name:74} -> {got}' + (f'   {ok}' if ok else ''), flush=True)


def call(host, path, method='GET', headers=None, body=None, port=P):
    c = http.client.HTTPConnection('127.0.0.1', port, timeout=20)
    c.putrequest(method, path, skip_host=True)
    c.putheader('Host', host)
    for k, v in (headers or {}).items():
        c.putheader(k, v)
    data = json.dumps(body).encode() if body is not None else b''
    if body is not None:
        c.putheader('Content-Type', 'application/json')
    if method == 'POST':
        c.putheader('Content-Length', str(len(data)))
    c.endheaders(data or None)
    r = c.getresponse()
    out = (r.status, r.read(), {k.lower(): v for k, v in r.getheaders()})
    c.close()
    return out


def login_cookie():
    c = http.client.HTTPConnection('127.0.0.1', P, timeout=20)
    body = json.dumps({'username': sec['username'], 'password': sec['password']}).encode()
    c.putrequest('POST', '/api/auth/login', skip_host=True)
    c.putheader('Host', O)
    c.putheader('Origin', f'http://{O}')
    c.putheader('Content-Type', 'application/json')
    c.putheader('Content-Length', str(len(body)))
    c.endheaders(body)
    r = c.getresponse()
    r.read()
    value = next(v.split(';')[0].split('=', 1)[1] for k, v in r.getheaders() if k.lower() == 'set-cookie' and v.startswith('odysseus_session='))
    c.close()
    return value


def enter(cookie):
    s, b, d = call(O, '/api/application-modes/projects/launch', 'POST', {'Origin': f'http://{O}', 'Cookie': f'odysseus_session={cookie}'})
    assert s == 200, (s, b[:80])
    path = json.loads(b)['url'].split(H, 1)[1]
    c = http.client.HTTPConnection('127.0.0.1', P, timeout=20)
    c.putrequest('GET', path, skip_host=True)
    for k, v in (('Host', H), ('Referer', f'http://{O}/'), ('Sec-Fetch-Mode', 'navigate'), ('Sec-Fetch-Dest', 'document')):
        c.putheader(k, v)
    c.endheaders()
    r = c.getresponse()
    r.read()
    assert r.status == 303, r.status
    value = next(v.split(';')[0].split('=', 1)[1] for k, v in r.getheaders() if k.lower() == 'set-cookie')
    c.close()
    return value


def state(grant, protected=False, bearer=None):
    h = {'Cookie': f'odysseus_hermes_grant={grant}'}
    if bearer:
        h['Authorization'] = f'Bearer {bearer}'
    s, b, d = call(H, '/api/profiles' if protected else '/api/health', 'GET', h)
    return f'{s} ' + ('GATE-DENIED' if d.get('x-odysseus-grant') == 'required' else 'passed-to-gateway')


def preview(action):
    r = subprocess.run([FULL, '-W', 'ignore', LAB + r'\tools\preview.py', action], env=env, capture_output=True, text=True, timeout=120)
    return (r.stdout or r.stderr).strip().splitlines()[-1:]


def wait_up(port, seconds=90):
    end = time.time() + seconds
    while time.time() < end:
        try:
            c = http.client.HTTPConnection('127.0.0.1', port, timeout=3)
            c.request('GET', '/api/health')
            c.getresponse().read()
            c.close()
            return True
        except OSError:
            time.sleep(1.5)
    return False


print('== setup: two fresh Odysseus sessions (C stays valid, D will expire while Odysseus is down)')
cookie_c, cookie_d = login_cookie(), login_cookie()
grant_c, grant_d = enter(cookie_c), enter(cookie_d)
record('C and D grants work before the restart', (state(grant_c), state(grant_d)), "('200 passed-to-gateway', '200 passed-to-gateway')")
record('earlier revoked grants A and B are denied before the restart', (state(old['grantA']), state(old['grantB'])), "('401 GATE-DENIED', '401 GATE-DENIED')")

print('== 1. Restart Odysseus (preview only). D\'s session expires while it is down')
print('   stop:', preview('stop'))
time.sleep(3)
sessions_path = LAB + r'\odysseus-preview\data\sessions.json'
data = json.load(open(sessions_path))
record('D session present in the preview sessions file before the edit', cookie_d in data, 'True')
data.pop(cookie_d)
json.dump(data, open(sessions_path, 'w'))
record('port 19800 is down during the restart window', not wait_up(P, 3), 'True')
print('   start:', preview('start'))
record('Odysseus is back up', wait_up(P, 120), 'True')
time.sleep(4)
record('C (Odysseus session survived the restart) still admitted', state(grant_c), '200 passed-to-gateway')
record('D (Odysseus session gone while down) is denied with no callback ever delivered', state(grant_d), '401 GATE-DENIED')
record('A and B stay revoked after the restart', (state(old['grantA']), state(old['grantB'])), "('401 GATE-DENIED', '401 GATE-DENIED')")
record('a brand-new login can launch Projects after the restart', state(enter(login_cookie())), '200 passed-to-gateway')
s, b, d = call(O, '/api/auth/logout', 'POST', {'Origin': f'http://{O}', 'Cookie': f'odysseus_session={cookie_c}'})
record('C Odysseus logout after the restart', s, '200')
record('C denied after logging out post-restart', state(grant_c), '401 GATE-DENIED')

print('== 2. Restart the Hermes gateway (preview only): the grant gate and Hermes authentication stay independent')
cookie_e = login_cookie()
grant_e = enter(cookie_e)
record('E admitted by the gate and by the gateway (old Hermes token)', state(grant_e, True, token), '200 passed-to-gateway')
subprocess.run([PYV, LAB + r'\tools\lab.py', 'stop'], env=env, capture_output=True, text=True, timeout=120)
time.sleep(3)
rl = subprocess.run([PYV, LAB + r'\tools\lab.py', 'start'], env=env, capture_output=True, text=True, timeout=180)
record('gateway is back up', wait_up(GW, 120), 'True')
end = time.time() + 120
while time.time() < end and not state(grant_e, True).startswith('401'):       # /api/health answers before the protected routes are ready (503)
    time.sleep(3)
record('E still passes the GATE; the gateway decides about its own token (lab gateway keeps it on disk)', state(grant_e, True, token), '200 passed-to-gateway')
record('E with no Hermes token: gateway still demands its own authentication', state(grant_e, True), '401 passed-to-gateway')
s, b, d = call(O, '/api/auth/logout', 'POST', {'Origin': f'http://{O}', 'Cookie': f'odysseus_session={cookie_e}'})
record('E Odysseus logout', s, '200')
record('E denied by the gate afterwards', state(grant_e), '401 GATE-DENIED')

json.dump(rows, open(LAB + r'\results\grants-restart-probe.json', 'w'), indent=1)
print('PASS', sum(r['ok'] == 'PASS' for r in rows), 'FAIL', sum(r['ok'] == 'FAIL' for r in rows), 'info', sum(r['ok'] == '' for r in rows))

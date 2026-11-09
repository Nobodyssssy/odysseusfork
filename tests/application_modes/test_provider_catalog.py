import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import socket
import sys
import threading
from types import SimpleNamespace
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from src.provider_adapters import fetch_hermes_providers, hermes_entries, model_ids, odysseus_entries, probe_local_models
from src.provider_catalog import SyncState, build_catalog, canonical_url, redact_url

SECRET = 'sk-SENTINEL-must-never-appear-0123456789'


def row(**kw):
    base = dict(id='e1', name='Local llama', base_url='http://localhost:8000/v1', api_key=None, is_enabled=True,
                cached_models='["ornith-gguf"]', pinned_models='["ornith-gguf"]', hidden_models=None, provider_auth_id=None)
    base.update(kw)
    return SimpleNamespace(**base)


def hermes_payload(*items):
    return {'providers': list(items), 'active_provider': 'x'}


def hermes_item(**kw):
    base = dict(id='lmstudio', display_name='LM Studio', has_key=False, is_oauth=False, is_self_hosted=True,
                base_url='http://127.0.0.1:8000/v1', models=[{'id': 'ornith-gguf', 'label': 'x'}], auth_error=None)
    base.update(kw)
    return base


class FakeModels(BaseHTTPRequestHandler):
    status = 200
    seen_auth = []

    def log_message(self, *args):
        pass

    def do_GET(self):
        FakeModels.seen_auth.append(self.headers.get('Authorization'))
        body = json.dumps({'data': [{'id': 'm1'}, {'id': 'm2'}]}).encode() if self.status == 200 else b'{}'
        self.send_response(self.status)
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def serve(status=200):
    handler = type('H', (FakeModels,), {'status': status})
    server = ThreadingHTTPServer(('127.0.0.1', 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


class CatalogTests(unittest.TestCase):
    def test_equivalent_local_urls_share_one_identity_and_credentials_in_urls_are_rejected(self):
        same = {canonical_url(u) for u in ('http://localhost:8000/v1', 'http://127.0.0.1:8000/v1/', 'HTTP://[::1]:8000/v1',
                                           'http://0.0.0.0:8000/v1/chat/completions', 'http://localhost:8000/v1/models')}
        self.assertEqual(same, {'http://127.0.0.1:8000/v1'})
        self.assertIsNone(canonical_url('http://user:pw@host/v1'))
        self.assertIsNone(canonical_url('file:///etc/passwd'))
        self.assertEqual(redact_url('https://u:pw@host:9/v1?key=abc#f'), 'https://host:9/v1')

    def test_the_same_local_server_added_in_both_apps_is_one_row_with_both_statuses(self):
        catalog = build_catalog(odysseus_entries([row()]), hermes_entries(hermes_payload(hermes_item())))
        self.assertEqual(len(catalog), 1)
        entry = catalog[0]
        self.assertEqual((entry['source'], entry['sync']), ('both', 'in_sync'))
        self.assertEqual(set(entry['status']), {'odysseus', 'hermes'})
        self.assertEqual(entry['models'], ['ornith-gguf'])

    def test_each_direction_is_discovered_and_sharing_is_opt_in(self):
        od_only = build_catalog(odysseus_entries([row()]), [])
        self.assertEqual((od_only[0]['source'], od_only[0]['sync']), ('odysseus', 'sharing_off'))
        state = SyncState(share={od_only[0]['id']: True})
        self.assertEqual(build_catalog(odysseus_entries([row()]), [], state)[0]['sync'], 'pending_hermes')
        he_only = build_catalog([], hermes_entries(hermes_payload(hermes_item())))
        self.assertEqual((he_only[0]['source'], he_only[0]['sync']), ('hermes', 'sharing_off'))
        state = SyncState(share={he_only[0]['id']: True})
        self.assertEqual(build_catalog([], hermes_entries(hermes_payload(hermes_item())), state)[0]['sync'], 'pending_odysseus')

    def test_duplicates_inside_one_app_do_not_create_extra_rows(self):
        catalog = build_catalog(odysseus_entries([row(id='a'), row(id='b', name='Same server again', base_url='http://127.0.0.1:8000/v1/')]), [])
        self.assertEqual((len(catalog), catalog[0]['duplicates']), (1, 1))

    def test_oauth_and_subscription_providers_are_never_shareable(self):
        items = [hermes_item(id='openai-codex', display_name='Codex', is_self_hosted=False, base_url=None, is_oauth=True, has_key=True),
                 hermes_item(id='claude-subscription-directsdk-experimental', display_name='Claude sub', is_self_hosted=False, base_url=None, has_key=True)]
        by_id = {e['name']: e for e in build_catalog([], hermes_entries(hermes_payload(*items)), SyncState(share={'x': True}))}
        self.assertEqual(by_id['Codex']['sync'], 'not_shareable_credential')
        self.assertEqual(by_id['Claude sub']['sync'], 'not_shareable')
        self.assertFalse(by_id['Codex']['portable'])
        chatgpt_like = build_catalog(odysseus_entries([row(base_url='https://chatgpt.com/backend', provider_auth_id='acct', api_key=None)]), [])
        self.assertEqual(chatgpt_like[0]['sync'], 'not_shareable_credential')

    def test_authentication_required_and_unreferenced_catalog_providers(self):
        remote_no_key = build_catalog(odysseus_entries([row(base_url='https://api.example.test/v1', cached_models='[]', pinned_models='[]')]), [])
        self.assertEqual(remote_no_key[0]['status']['odysseus'], 'requires_authentication')
        unconfigured = hermes_item(id='anthropic', is_self_hosted=False, base_url=None, has_key=False)
        self.assertEqual(hermes_entries(hermes_payload(unconfigured)), [])
        never_logged_in = hermes_item(id='copilot', display_name='GitHub Copilot', is_self_hosted=False, base_url=None, has_key=False, is_oauth=True)
        self.assertEqual(hermes_entries(hermes_payload(never_logged_in)), [])

    def test_known_openai_compatible_cloud_provider_matches_across_apps(self):
        he = hermes_item(id='openrouter', display_name='OpenRouter', is_self_hosted=False, base_url=None, has_key=True, models=[{'id': 'a/b'}])
        od = row(id='o', name='OR', base_url='https://openrouter.ai/api/v1/', api_key=SECRET, cached_models='["a/b","c/d"]', pinned_models=None)
        catalog = build_catalog(odysseus_entries([od]), hermes_entries(hermes_payload(he)))
        self.assertEqual((len(catalog), catalog[0]['source'], catalog[0]['sync']), (1, 'both', 'models_differ'))
        self.assertEqual(catalog[0]['models_by_app'], {'odysseus': ['a/b', 'c/d'], 'hermes': ['a/b']})

    def test_api_keys_never_reach_the_catalog_or_its_serialisation(self):
        od = odysseus_entries([row(api_key=SECRET, base_url='https://api.example.test/v1', name='Cloud', cached_models='["m"]')])
        he = hermes_entries(hermes_payload(hermes_item(id='openrouter', is_self_hosted=False, base_url=None, has_key=True,
                                                       auth_error=SECRET, models=[])))
        everything = json.dumps(build_catalog(od, he)) + repr(od) + repr(he)
        self.assertNotIn(SECRET, everything)
        self.assertTrue(od[0].has_credential)

    def test_hidden_models_are_excluded_and_state_survives_reload_and_corruption(self):
        entries = odysseus_entries([row(cached_models='["a","b"]', pinned_models='["a"]', hidden_models='["b"]')])
        self.assertEqual(entries[0].models, ('a',))
        state = SyncState(share={'ep:x': True}, tombstones={'ep:y': {'by': 'hermes', 'fingerprint': 'f'}})
        reloaded = SyncState.load(json.loads(json.dumps(state.dump())))
        self.assertEqual((reloaded.share, reloaded.tombstones), (state.share, state.tombstones))
        for garbage in (None, [], {'version': 2}, {'version': 1, 'share': 'x', 'tombstones': []}):
            self.assertEqual(SyncState.load(garbage).dump()['share'], {})

    def test_a_deleted_provider_is_not_resurrected_and_is_reported(self):
        entries = odysseus_entries([row()])
        identity = build_catalog(entries, [])[0]['id']
        state = SyncState(share={identity: True}, tombstones={identity: {'by': 'hermes', 'fingerprint': 'f'}})
        self.assertEqual(build_catalog(entries, [], state)[0]['sync'], 'deleted_on_hermes')

    def test_catalog_is_idempotent_and_ordered(self):
        entries = odysseus_entries([row(id='b', base_url='http://localhost:9/v1'), row(id='a')])
        first = build_catalog(entries, [])
        self.assertEqual(first, build_catalog(list(reversed(entries)), []))

    def test_model_id_parsing_is_tolerant(self):
        self.assertEqual(model_ids('not json'), ())
        self.assertEqual(model_ids([{'id': 'a'}, 'a', 5, {'x': 1}, 'b']), ('a', 'b'))

    def test_hermes_custom_providers_resolve_their_url_from_config_and_match_odysseus(self):
        item = {'id': 'custom:ody-1', 'display_name': 'Local llama', 'has_key': False, 'is_oauth': False, 'is_custom': True,
                'configurable': False, 'key_source': 'none', 'models': [{'id': 'ornith-gguf'}]}      # no base_url key, as Hermes returns
        self.assertEqual(hermes_entries(hermes_payload(item)) and hermes_entries(hermes_payload(item))[0].canonical, None)
        entries = hermes_entries(hermes_payload(item), {'custom:ody-1': 'http://127.0.0.1:8000/v1'})
        self.assertEqual(entries[0].canonical, 'http://127.0.0.1:8000/v1')
        catalog = build_catalog(odysseus_entries([row()]), entries)
        self.assertEqual((len(catalog), catalog[0]['sync']), (1, 'in_sync'))
        self.assertEqual(entries[0].auth, 'none')          # a keyless loopback server needs no key, so it can be Offline, not "needs auth"

    def test_config_reader_returns_urls_only_and_never_a_secret(self):
        import tempfile
        from src.provider_adapters import read_hermes_provider_urls
        text = ('model:\n  default: x\n  api_key: ' + SECRET + '\nproviders:\n  "custom:a":\n    base_url: "http://127.0.0.1:1/v1"\n    api_key: ' + SECRET
                + '\n  lmstudio:\n    base_url: http://localhost:1234/v1\n  nourl:\n    api_key: ' + SECRET + '\ncustom_providers:\n  - name: My Box\n    base_url: http://127.0.0.1:2/v1\n    api_key: ' + SECRET + '\n')
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[2] / '.release-tests') as directory:
            path = Path(directory) / 'config.yaml'
            path.write_text(text, encoding='utf-8')
            urls = read_hermes_provider_urls(path)
            self.assertEqual(urls, {'custom:a': 'http://127.0.0.1:1/v1', 'lmstudio': 'http://localhost:1234/v1', 'custom:my-box': 'http://127.0.0.1:2/v1'})
            self.assertNotIn(SECRET, repr(urls))
            self.assertEqual(read_hermes_provider_urls(Path(directory) / 'missing.yaml'), {})
            path.write_text('::: not yaml [', encoding='utf-8')
            self.assertEqual(read_hermes_provider_urls(path), {})


    def test_hermes_built_in_keyed_providers_are_shareable_and_solstice_style_plugins_are_not(self):
        zen = hermes_item(id='opencode-zen', display_name='OpenCode Zen', has_key=True, is_self_hosted=False, is_custom=False,
                          base_url=None, models=[{'id': 'm1'}, {'id': 'm2'}])
        plugin = hermes_item(id='solstice', display_name='Solstice', has_key=True, is_self_hosted=False, is_plugin_provider=True,
                             base_url=None, models=[{'id': 's1'}])
        entries = hermes_entries(hermes_payload(zen, plugin))
        by_id = {e.native_id: e for e in entries}
        self.assertEqual(by_id['opencode-zen'].canonical, 'https://opencode.ai/zen/v1')
        self.assertTrue(by_id['opencode-zen'].portable)
        self.assertFalse(by_id['solstice'].portable)
        catalog = {r['name']: r for r in build_catalog([], entries, SyncState())}
        self.assertEqual(catalog['OpenCode Zen']['sync'], 'sharing_off')
        self.assertEqual(catalog['Solstice']['sync'], 'not_shareable_protocol')
        self.assertNotIn(SECRET, json.dumps(catalog))


class ProbeTests(unittest.TestCase):
    def setUp(self):
        FakeModels.seen_auth = []

    def test_local_server_is_discovered_without_sending_credentials(self):
        server = serve()
        try:
            url = canonical_url(f'http://localhost:{server.server_port}/v1')
            self.assertEqual(probe_local_models(url), ('online', ('m1', 'm2')))
            self.assertEqual(FakeModels.seen_auth, [None])
        finally:
            server.shutdown()
            server.server_close()

    def test_auth_required_offline_and_remote_targets(self):
        server = serve(status=401)
        try:
            self.assertEqual(probe_local_models(f'http://127.0.0.1:{server.server_port}/v1')[0], 'requires_authentication')
        finally:
            server.shutdown()
            server.server_close()
        with socket.socket() as s:
            s.bind(('127.0.0.1', 0))
            free = s.getsockname()[1]
        self.assertEqual(probe_local_models(f'http://127.0.0.1:{free}/v1')[0], 'offline')
        self.assertEqual(probe_local_models('https://api.example.test/v1')[0], 'error')

    def test_hermes_listing_states(self):
        self.assertEqual(fetch_hermes_providers('http://192.168.1.5:8787/')[1], 'unavailable')
        with socket.socket() as s:
            s.bind(('127.0.0.1', 0))
            free = s.getsockname()[1]
        self.assertEqual(fetch_hermes_providers(f'http://127.0.0.1:{free}/')[1], 'offline')
        server = serve(status=401)
        try:
            self.assertEqual(fetch_hermes_providers(f'http://127.0.0.1:{server.server_port}/')[1], 'requires_authentication')
        finally:
            server.shutdown()
            server.server_close()


if __name__ == '__main__':
    unittest.main()

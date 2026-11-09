"""HTTP boundary of provider sharing: only a local administrator in a real browser, never the Agent."""
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from src.provider_sync import SyncError

SECRET = 'sk-SENTINEL-must-never-appear-0123456789'
ORIGIN = {'Origin': 'http://127.0.0.1:17000'}


class ProviderRouteTests(unittest.TestCase):
    def client(self, address='127.0.0.1', admin=True, user='fixture', service='default'):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from routes.provider_sync_routes import make_provider_sync_router
        app = FastAPI()
        app.state.auth_manager = Mock(is_configured=True)
        app.state.auth_manager.is_admin.return_value = admin

        @app.middleware('http')
        async def identity(request, call_next):
            request.state.current_user = user
            return await call_next(request)
        if service == 'default':
            service = Mock()
            service.snapshot.return_value = {'providers': [{'id': 'ep:http://127.0.0.1:8000/v1', 'name': 'Local'}], 'applications': {}}
            service.set_share.return_value = {'id': 'x', 'share_enabled': True}
            service.apply.return_value = {'result': 'added'}
            service.apply_all.return_value = []
            service.test.return_value = {'state': 'online'}
        app.include_router(make_provider_sync_router(lambda: service))
        return TestClient(app, base_url='http://127.0.0.1:17000', client=(address, 40000)), service

    WRITES = [('/api/provider-sync/refresh', None), ('/api/provider-sync/share', {'id': 'x', 'enabled': True}),
              ('/api/provider-sync/apply', {'id': 'x'}), ('/api/provider-sync/apply-all', None), ('/api/provider-sync/test', {'id': 'x'})]

    def post(self, client, path, body, headers=None):
        headers = ORIGIN if headers is None else headers
        return client.post(path, json=body, headers=headers) if body is not None else client.post(path, headers=headers)

    def test_a_local_admin_in_a_browser_can_read_and_act(self):
        client, service = self.client()
        self.assertEqual(client.get('/api/provider-sync').status_code, 200)
        for path, body in self.WRITES:
            self.assertEqual(self.post(client, path, body).status_code, 200, path)
        service.snapshot.assert_called()

    def test_the_agent_tool_identity_api_tokens_and_internal_header_are_refused_everywhere(self):
        for user in ('internal-tool', 'api', 'system', 'demo'):
            client, service = self.client(user=user)
            self.assertEqual(client.get('/api/provider-sync').status_code, 403, user)
            for path, body in self.WRITES:
                self.assertEqual(self.post(client, path, body).status_code, 403, (user, path))
        client, service = self.client()
        for header in ({'Authorization': 'Bearer x'}, {'X-Odysseus-Internal-Token': 'x'}):
            self.assertEqual(client.get('/api/provider-sync', headers=header).status_code, 403)
            for path, body in self.WRITES:
                self.assertEqual(self.post(client, path, body, {**ORIGIN, **header}).status_code, 403, path)
        service.apply.assert_not_called()
        service.set_share.assert_not_called()

    def test_non_admins_remote_clients_proxied_and_cross_origin_requests_are_refused(self):
        client, service = self.client(admin=False)
        self.assertNotEqual(client.get('/api/provider-sync').status_code, 200)
        for path, body in self.WRITES:
            self.assertNotEqual(self.post(client, path, body).status_code, 200, path)
        client, service = self.client(address='192.0.2.4')
        self.assertEqual(client.get('/api/provider-sync').status_code, 403)
        client, service = self.client()
        self.assertEqual(client.get('/api/provider-sync', headers={'X-Forwarded-For': '203.0.113.9'}).status_code, 403)
        for origin in (None, 'https://evil.example', 'http://127.0.0.1:7000', 'null'):
            headers = {'Origin': origin} if origin else {}
            self.assertEqual(self.post(client, '/api/provider-sync/apply', {'id': 'x'}, headers).status_code, 403, origin)
        self.assertEqual(client.get('/api/provider-sync', headers={'Host': 'attacker.example:17000'}).status_code, 403)
        service.apply.assert_not_called()

    def test_refusals_from_the_service_become_conflicts_and_are_not_leaked_as_errors(self):
        client, service = self.client()
        service.apply.side_effect = SyncError('This provider was deleted on the other side; re-enable sharing to restore it.')
        response = self.post(client, '/api/provider-sync/apply', {'id': 'x'})
        self.assertEqual(response.status_code, 409)
        self.assertIn('deleted', response.json()['detail'])

    def test_not_configured_is_a_404_and_responses_carry_no_secret(self):
        client, _ = self.client(service=None)
        self.assertEqual(client.get('/api/provider-sync').status_code, 404)
        client, service = self.client()
        service.snapshot.return_value = {'providers': [{'name': 'Cloud', 'has_credential': True}]}
        self.assertNotIn(SECRET, json.dumps(client.get('/api/provider-sync').json()))

    def test_malformed_bodies_are_rejected(self):
        client, _ = self.client()
        self.assertEqual(self.post(client, '/api/provider-sync/share', {'id': 'x'}).status_code, 422)
        self.assertEqual(self.post(client, '/api/provider-sync/apply', {}).status_code, 422)


if __name__ == '__main__':
    unittest.main()

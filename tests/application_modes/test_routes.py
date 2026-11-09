"""HTTP boundary tests for the opt-in local-only mode routes."""
from pathlib import Path
import sys
import unittest
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))


class RouteTests(unittest.TestCase):
    def client(self, address='127.0.0.1', admin=True, user='fixture'):
        import src.application_modes as modes
        self.assertTrue(hasattr(modes, 'make_router'), 'mode HTTP routes are missing')
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        app = FastAPI()
        app.state.auth_manager = Mock(is_configured=True)
        app.state.auth_manager.is_admin.return_value = admin
        @app.middleware('http')
        async def identity(request, call_next):
            request.state.current_user = user
            return await call_next(request)
        service = Mock()
        service.status.return_value = {'enabled': True, 'state': 'ready'}
        service.start.return_value = {'enabled': True, 'state': 'starting'}
        app.include_router(modes.make_router(service))
        return TestClient(app, base_url='http://127.0.0.1:17000', client=(address, 40000)), service

    def test_local_health_uses_existing_service(self):
        client, service = self.client()
        self.assertEqual(client.get('/api/application-modes').json()['state'], 'ready')
        service.start.assert_not_called()

    def test_localhost_name_is_accepted_but_other_hostnames_are_not(self):
        client, service = self.client()
        self.assertEqual(client.get('/api/application-modes', headers={'Host': 'localhost:17000'}).status_code, 200)
        self.assertEqual(client.get('/api/application-modes', headers={'Host': 'attacker.example:17000'}).status_code, 403)

    def test_remote_clients_are_denied(self):
        client, service = self.client(address='192.0.2.4')
        self.assertEqual(client.get('/api/application-modes').status_code, 403)
        self.assertEqual(client.post('/api/application-modes/hermes/start', headers={'Origin': 'http://127.0.0.1:17000'}).status_code, 403)
        service.start.assert_not_called()

    def test_cross_origin_and_missing_origin_start_are_denied(self):
        client, service = self.client()
        for origin in [None, 'https://evil.example', 'http://127.0.0.1:7000', 'null']:
            headers = {'Origin': origin} if origin else {}
            self.assertEqual(client.post('/api/application-modes/hermes/start', headers=headers).status_code, 403)
        service.start.assert_not_called()

    def test_same_origin_admin_can_start(self):
        client, service = self.client()
        self.assertEqual(client.post('/api/application-modes/hermes/start', headers={'Origin': 'http://127.0.0.1:17000'}).status_code, 200)
        service.start.assert_called_once()

    def test_non_admin_cannot_start(self):
        client, service = self.client(admin=False)
        self.assertEqual(client.post('/api/application-modes/hermes/start', headers={'Origin': 'http://127.0.0.1:17000'}).status_code, 403)
        service.start.assert_not_called()

    def test_agent_internal_identity_and_forwarded_requests_cannot_launch(self):
        for user in ('internal-tool', 'api'):
            client, service = self.client(user=user)
            self.assertEqual(client.post('/api/application-modes/hermes/start', headers={'Origin': 'http://127.0.0.1:17000'}).status_code, 403)
            service.start.assert_not_called()
        client, service = self.client()
        self.assertEqual(client.post('/api/application-modes/hermes/start', headers={'Origin': 'http://127.0.0.1:17000', 'X-Forwarded-For': '192.0.2.1'}).status_code, 403)
        service.start.assert_not_called()

    def test_start_failure_is_recoverable_without_restarting(self):
        client, service = self.client()
        service.start.side_effect = RuntimeError('Occupied port')
        response = client.post('/api/application-modes/hermes/start', headers={'Origin': 'http://127.0.0.1:17000'})
        self.assertEqual(response.status_code, 409)
        self.assertIn('Occupied port', response.text)


if __name__ == '__main__':
    unittest.main()

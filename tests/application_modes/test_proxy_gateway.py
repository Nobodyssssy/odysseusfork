"""Gateway-mode Hermes proxy: Host/Origin policy, WebSocket relay, auth pass-through, and legacy webui mode unchanged.

Needs the ``websockets`` package; the relay tests are skipped (the 501 path is tested instead) where it is missing.
"""
import asyncio
import json
import os
from pathlib import Path
import socket
import sys
import threading
import time
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

import http.client
import uvicorn

from src import hermes_proxy
from src.hermes_proxy import HermesHostProxy, ProxyConfigError, ProxyPolicy, install_hermes_proxy

try:
    import websockets
    from websockets.asyncio.server import serve
    from websockets.exceptions import ConnectionClosed, InvalidStatus
except ImportError:                                         # pragma: no cover
    websockets = None

if websockets is None:
    raise unittest.SkipTest('websockets is not installed; the gateway-mode proxy and its test gateway need it')

TOKEN = 'good-token'
BIG = 5 * 1024 * 1024


def free_port():
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        return sock.getsockname()[1]


class FakeGateway:
    """Behaves like the official gateway where it matters: only its own authority is an acceptable Host/Origin,
    the session token is required, and it echoes WebSocket frames. Records everything it sees."""

    def __init__(self, public_host=None):
        self.port = free_port()
        self.authority = public_host or f'127.0.0.1:{self.port}'   # the Host/Origin this gateway insists on
        self.requests = []
        self.ws_closed = threading.Event()
        self.ws_close_info = None
        self.loop = None
        self.started = threading.Event()

    def seen(self):
        return list(self.requests)

    def _record(self, request, kind):
        self.requests.append({'kind': kind, 'path': request.path, 'headers': {k.lower(): v for k, v in request.headers.raw_items()}})

    def _reply(self, connection, status, body=b'', headers=()):
        response = connection.respond(status, body.decode() if isinstance(body, bytes) else body)
        for name, value in headers:
            response.headers[name] = value
        return response

    async def _guard(self, connection, request):
        self._record(request, 'ws' if request.headers.get('Upgrade', '').lower() == 'websocket' else 'http')
        if request.headers.get('Host') != self.authority:
            return self._reply(connection, 400, 'bad host')
        origin = request.headers.get('Origin')
        if origin is not None and origin != f'http://{self.authority}':
            return self._reply(connection, 403, 'bad origin')
        cookie = request.headers.get('Cookie', '')
        authorised = request.headers.get('Authorization') == f'Bearer {TOKEN}' or f'sess={TOKEN}' in cookie or f'token={TOKEN}' in request.path
        if request.path.startswith('/api/') and not authorised:
            return self._reply(connection, 401, 'unauthorised')
        if request.headers.get('Upgrade', '').lower() == 'websocket':
            return None
        if request.path.startswith('/api/echo'):
            return self._reply(connection, 200, json.dumps({'host': request.headers.get('Host'), 'origin': origin,
                                                            'referer': request.headers.get('Referer')}),
                               [('Content-Type', 'application/json'), ('Access-Control-Allow-Origin', '*'),
                                ('Access-Control-Allow-Credentials', 'true'), ('Set-Cookie', 'sess=fresh; HttpOnly')])
        return self._reply(connection, 200, 'gateway-http')

    async def _handler(self, ws):
        try:
            async for message in ws:
                if message == 'close4001':
                    await ws.close(4001, 'bye')
                    return
                if message == 'hang':
                    await asyncio.sleep(30)
                await ws.send(message)
        finally:
            self.ws_close_info = (ws.close_code, ws.close_reason)
            self.ws_closed.set()

    def _run(self):
        async def main():
            self.loop = asyncio.get_running_loop()
            self.stop = asyncio.Event()
            async with serve(self._handler, '127.0.0.1', self.port, process_request=self._guard,
                             select_subprotocol=lambda connection, offered: 'hermes.v1' if 'hermes.v1' in offered else None,
                             max_size=64 * 1024 * 1024, compression=None):
                self.started.set()
                await self.stop.wait()
        asyncio.run(main())

    def start(self):
        threading.Thread(target=self._run, daemon=True).start()
        assert self.started.wait(10)

    def shutdown(self):
        if self.loop:
            self.loop.call_soon_threadsafe(self.stop.set)


async def odysseus(scope, receive, send):
    if scope['type'] == 'websocket':
        await receive()
        return await send({'type': 'websocket.close', 'code': 1008})
    await send({'type': 'http.response.start', 'status': 200, 'headers': [(b'content-type', b'text/plain')]})
    await send({'type': 'http.response.body', 'body': b'ODYSSEUS'})


class Served:
    def __init__(self, app, port):
        self.port = port
        self.server = uvicorn.Server(uvicorn.Config(app, host='127.0.0.1', port=port, log_level='error', ws_max_size=64 * 1024 * 1024, ws=os.environ.get('PROXY_TEST_WS', 'auto')))
        self.thread = threading.Thread(target=self.server.run, daemon=True)

    def __enter__(self):
        self.thread.start()
        deadline = time.time() + 10
        while not self.server.started and time.time() < deadline:
            time.sleep(0.05)
        assert self.server.started
        return self

    def __exit__(self, *exc):
        self.server.should_exit = True
        self.thread.join(10)


def raw(port, request, read=True):
    """Send raw bytes, return (status, headers, socket)."""
    sock = socket.create_connection(('127.0.0.1', port), timeout=5)
    sock.sendall(request.encode('latin-1'))
    data = b''
    while b'\r\n\r\n' not in data:
        chunk = sock.recv(4096)
        if not chunk:
            break
        data += chunk
    head, _, rest = data.partition(b'\r\n\r\n')
    lines = head.decode('latin-1').split('\r\n')
    status = int(lines[0].split()[1]) if lines and lines[0] else 0
    headers = {k.lower(): v for k, _, v in (line.partition(': ') for line in lines[1:])}
    return status, headers, sock


def handshake(host, *extra, path='/api/ws', origin='default', token=True):
    lines = [f'GET {path}{"?token=" + TOKEN if token else ""} HTTP/1.1', f'Host: {host}', 'Upgrade: websocket', 'Connection: Upgrade',
             'Sec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==', 'Sec-WebSocket-Version: 13']
    if origin == 'default':
        origin = 'http://' + host
    if origin is not None:
        lines.append(f'Origin: {origin}')
    lines.extend(extra)
    return '\r\n'.join(lines) + '\r\n\r\n'


def http_request(method, host, path, *extra, body=b''):
    lines = [f'{method} {path} HTTP/1.1', f'Host: {host}', 'Connection: close', *([f'Content-Length: {len(body)}'] if method != 'GET' else []), *extra]
    return '\r\n'.join(lines) + '\r\n\r\n'


def fetch(port, host, path, method='GET', headers=None, body=b''):
    conn = http.client.HTTPConnection('127.0.0.1', port, timeout=10)
    conn.putrequest(method, path, skip_host=True, skip_accept_encoding=True)
    conn.putheader('Host', host)
    for name, value in (headers or {}).items():
        conn.putheader(name, value)
    if body or method not in ('GET', 'HEAD'):
        conn.putheader('Content-Length', str(len(body)))
    conn.endheaders(body)
    response = conn.getresponse()
    data = response.read()
    result = (response.status, dict((k.lower(), v) for k, v in response.getheaders()), data)
    conn.close()
    return result


class GatewayProxyBase(unittest.TestCase):
    policy = ProxyPolicy()
    mode = 'gateway'

    @classmethod
    def setUpClass(cls):
        cls.gateway = FakeGateway()
        cls.gateway.start()
        cls.port = free_port()
        cls.host = f'hermes.localhost:{cls.port}'
        app = HermesHostProxy(odysseus, f'http://127.0.0.1:{cls.gateway.port}', mode=cls.mode, policy=cls.policy)
        cls.served = Served(app, cls.port)
        cls.served.__enter__()

    @classmethod
    def tearDownClass(cls):
        cls.served.__exit__()
        cls.gateway.shutdown()

    def setUp(self):
        self.gateway.requests.clear()
        self.gateway.ws_closed.clear()
        self.gateway.ws_close_info = None

    def assertNoGatewayTraffic(self):
        self.assertEqual(self.gateway.seen(), [], 'a refused request must never reach the gateway')


class GatewayHttpTests(GatewayProxyBase):
    def test_http_is_forwarded_with_the_gateways_own_authority(self):
        status, headers, body = fetch(self.port, self.host, '/api/echo', headers={
            'Origin': f'http://{self.host}', 'Referer': f'http://{self.host}/page', 'Cookie': f'sess={TOKEN}'})
        data = json.loads(body)
        self.assertEqual(status, 200)
        self.assertEqual(data['host'], self.gateway.authority)
        self.assertEqual(data['origin'], f'http://{self.gateway.authority}')
        self.assertEqual(data['referer'], f'http://{self.gateway.authority}/page')

    def test_gateway_authentication_is_untouched(self):
        status, _, _ = fetch(self.port, self.host, '/api/echo')
        self.assertEqual(status, 401)                                              # the gateway refused it, not the proxy
        seen = self.gateway.seen()[-1]['headers']
        self.assertNotIn('authorization', seen)
        self.assertNotIn('cookie', seen)                                           # the proxy never invents a credential
        status, headers, _ = fetch(self.port, self.host, '/api/echo', headers={'Cookie': f'sess={TOKEN}'})
        self.assertEqual(status, 200)
        self.assertIn('sess=fresh', headers['set-cookie'])                         # gateway Set-Cookie reaches the browser
        status, _, _ = fetch(self.port, self.host, '/api/echo')
        self.assertEqual(status, 401)                                              # and the proxy did not keep that cookie
        self.assertNotIn('cookie', self.gateway.seen()[-1]['headers'])

    def test_cors_grants_are_dropped(self):
        _, headers, _ = fetch(self.port, self.host, '/api/echo', headers={'Cookie': f'sess={TOKEN}'})
        self.assertFalse([name for name in headers if name.startswith('access-control-')])

    def test_foreign_and_opaque_origins_are_refused(self):
        for origin in ('http://evil.test', 'null', f'http://hermes.localhost:{self.port + 1}', 'http://hermes.localhost',
                       f'https://{self.host}', f'http://127.0.0.1:{self.port}', f'http://localhost:{self.port}',
                       f'http://{self.host}.evil.test', f'http://evil.test:{self.port}', ''):
            with self.subTest(origin=origin):
                status, _, body = fetch(self.port, self.host, '/api/echo', 'POST', {'Origin': origin, 'Cookie': f'sess={TOKEN}'})
                self.assertEqual(status, 403, body)
        self.assertNoGatewayTraffic()

    def test_navigation_without_origin_and_sec_fetch_rules(self):
        self.assertEqual(fetch(self.port, self.host, '/', headers={'Sec-Fetch-Site': 'none'})[0], 200)
        self.assertEqual(fetch(self.port, self.host, '/', headers={'Sec-Fetch-Site': 'cross-site'})[0], 200)   # read-only GET only
        self.gateway.requests.clear()
        for site in ('cross-site', 'same-site'):
            self.assertEqual(fetch(self.port, self.host, '/api/echo', 'POST', {'Sec-Fetch-Site': site, 'Cookie': f'sess={TOKEN}'})[0], 403)
        self.assertNoGatewayTraffic()
        self.assertEqual(fetch(self.port, self.host, '/api/echo', 'POST', {'Sec-Fetch-Site': 'same-origin', 'Cookie': f'sess={TOKEN}'})[0], 200)

    def test_forged_hosts_never_reach_the_gateway(self):
        for host in ('evil.test', f'evil.test:{self.port}', f'hermes.localhost.evil.test:{self.port}', f'hermes.localhost:{self.port + 1}',
                     f'evil.hermes.localhost:{self.port}', '127.0.0.1', f'127.0.0.1:{self.gateway.port}', f'localhost:{self.port}',
                     f'hermes.localhost:{self.port}.evil.test', f'hermes.localhost@evil.test:{self.port}'):
            with self.subTest(host=host):
                status, _, body = fetch(self.port, host, '/api/echo', headers={'Cookie': f'sess={TOKEN}'})
                self.assertEqual(body, b'ODYSSEUS')                                # routed to Odysseus, never to Hermes
        self.assertNoGatewayTraffic()

    def test_forwarding_headers_are_refused(self):
        for name in ('Forwarded', 'X-Forwarded-For', 'X-Forwarded-Host', 'X-Forwarded-Proto', 'X-Forwarded-Port', 'X-Real-IP'):
            with self.subTest(header=name):
                self.assertEqual(fetch(self.port, self.host, '/api/echo', headers={name: '203.0.113.9'})[0], 403)
        self.assertNoGatewayTraffic()

    def test_duplicate_host_or_origin_is_refused(self):
        status, _, _ = raw(self.port, http_request('GET', self.host, '/', f'Host: {self.host}'))
        self.assertIn(status, (400, 403))
        status, _, _ = raw(self.port, http_request('POST', self.host, '/api/echo', f'Origin: http://{self.host}', 'Origin: http://evil.test'))
        self.assertEqual(status, 400)
        self.assertNoGatewayTraffic()

    def test_gateway_down_is_a_502(self):
        port = free_port()
        app = HermesHostProxy(odysseus, f'http://127.0.0.1:{free_port()}', mode='gateway')
        with Served(app, port):
            self.assertEqual(fetch(port, f'hermes.localhost:{port}', '/api/echo')[0], 502)


@unittest.skipIf(websockets is None, 'websockets is not installed')
class GatewayWebSocketTests(GatewayProxyBase):
    def url(self, path='/api/ws', token=True):
        return f'ws://hermes.localhost:{self.port}{path}{"?token=" + TOKEN if token else ""}'

    def connect(self, **kwargs):
        kwargs.setdefault('origin', f'http://{self.host}')
        kwargs.setdefault('max_size', None)
        return websockets.connect(self.url(), host='127.0.0.1', port=self.port, open_timeout=5, **kwargs)

    def test_text_binary_and_subprotocol_roundtrip(self):
        async def go():
            async with self.connect(subprotocols=['other', 'hermes.v1'], additional_headers={'Cookie': f'sess={TOKEN}'}) as ws:
                self.assertEqual(ws.subprotocol, 'hermes.v1')
                await ws.send('hello')
                self.assertEqual(await ws.recv(), 'hello')
                await ws.send(b'\x00\x01\xff')
                self.assertEqual(await ws.recv(), b'\x00\x01\xff')
        asyncio.run(go())
        seen = self.gateway.seen()[0]['headers']
        self.assertEqual(seen['host'], self.gateway.authority)
        self.assertEqual(seen['origin'], f'http://{self.gateway.authority}')
        self.assertEqual(seen['cookie'], f'sess={TOKEN}')                          # the browser's own credential, forwarded verbatim
        self.assertNotIn('authorization', seen)

    def test_large_frame(self):
        async def go():
            async with self.connect() as ws:
                payload = b'x' * BIG
                await ws.send(payload)
                self.assertEqual(await ws.recv(), payload)
        asyncio.run(go())

    def test_gateway_rejects_missing_credentials_and_the_client_sees_it(self):
        async def go():
            with self.assertRaises(InvalidStatus) as caught:
                async with websockets.connect(self.url(token=False), host='127.0.0.1', port=self.port, origin=f'http://{self.host}'):
                    pass
            return caught.exception.response.status_code
        self.assertEqual(asyncio.run(go()), 401)

    def test_close_codes_propagate_both_ways(self):
        async def go():
            async with self.connect() as ws:
                await ws.send('close4001')
                with self.assertRaises(ConnectionClosed):
                    await ws.recv()
                return ws.close_code, ws.close_reason
        self.assertEqual(asyncio.run(go()), (4001, 'bye'))

        self.assertTrue(self.gateway.ws_closed.wait(5))
        self.gateway.ws_closed.clear()
        self.gateway.ws_close_info = None

        async def client_closes():
            async with self.connect() as ws:
                await ws.send('x')
                await ws.recv()
                await ws.close(4002, 'done')
        asyncio.run(client_closes())
        self.assertTrue(self.gateway.ws_closed.wait(5))
        self.assertEqual(self.gateway.ws_close_info[0], 4002)

    def test_abrupt_client_disconnect_releases_the_gateway_connection(self):
        status, _, sock = raw(self.port, handshake(self.host))
        self.assertEqual(status, 101)
        sock.close()                                                              # no close frame, like a killed tab
        self.assertTrue(self.gateway.ws_closed.wait(5), 'gateway side stayed open after the browser vanished')

    def test_gateway_vanishing_closes_the_client(self):
        gateway = FakeGateway()
        gateway.start()
        port = free_port()
        app = HermesHostProxy(odysseus, f'http://127.0.0.1:{gateway.port}', mode='gateway')
        with Served(app, port):
            async def go():
                async with websockets.connect(f'ws://hermes.localhost:{port}/api/ws?token={TOKEN}', host='127.0.0.1', port=port,
                                              origin=f'http://hermes.localhost:{port}') as ws:
                    await ws.send('hang')
                    gateway.shutdown()
                    with self.assertRaises(ConnectionClosed):
                        await asyncio.wait_for(ws.recv(), 10)
            asyncio.run(go())

    def test_invalid_origins_are_refused_before_the_gateway(self):
        for origin in ('http://evil.test', 'null', f'http://hermes.localhost:{self.port + 1}', f'http://localhost:{self.port}',
                       f'http://127.0.0.1:{self.gateway.port}', f'https://{self.host}', None):
            with self.subTest(origin=origin):
                status, _, sock = raw(self.port, handshake(self.host, origin=origin))
                sock.close()
                self.assertEqual(status, 403)
        self.assertNoGatewayTraffic()

    def test_rebinding_style_requests_are_refused_or_routed_away(self):
        for host in ('evil.test', f'evil.test:{self.port}', f'hermes.localhost:{self.port + 1}'):
            with self.subTest(host=host):
                status, _, sock = raw(self.port, handshake(host))
                sock.close()
                self.assertNotEqual(status, 101)                                   # Odysseus' own WS handler closes it
        self.assertNoGatewayTraffic()

    def test_forwarding_headers_and_cross_site_fetch_metadata(self):
        for extra in (('X-Forwarded-For: 203.0.113.9',), ('Forwarded: for=203.0.113.9',), ('X-Real-IP: 203.0.113.9',),
                      ('Sec-Fetch-Site: cross-site',), ('Sec-Fetch-Site: same-site',)):
            with self.subTest(extra=extra):
                status, _, sock = raw(self.port, handshake(self.host, *extra))
                sock.close()
                self.assertEqual(status, 403)
        self.assertNoGatewayTraffic()

    def test_duplicate_origin_is_refused(self):
        status, _, sock = raw(self.port, handshake(self.host, 'Origin: http://evil.test'))
        sock.close()
        self.assertEqual(status, 400)
        self.assertNoGatewayTraffic()

    def test_same_origin_fetch_metadata_is_accepted(self):
        status, _, sock = raw(self.port, handshake(self.host, 'Sec-Fetch-Site: same-origin'))
        sock.close()
        self.assertEqual(status, 101)

    def test_websocket_support_missing_fails_closed(self):
        with mock.patch.object(hermes_proxy, '_websockets', return_value=None):
            status, _, sock = raw(self.port, handshake(self.host))
            sock.close()
        self.assertEqual(status, 501)
        self.assertNoGatewayTraffic()

    def test_gateway_down_is_a_502(self):
        port = free_port()
        app = HermesHostProxy(odysseus, f'http://127.0.0.1:{free_port()}', mode='gateway')
        with Served(app, port):
            status, _, sock = raw(port, handshake(f'hermes.localhost:{port}'))
            sock.close()
        self.assertEqual(status, 502)


@unittest.skipIf(websockets is None, 'websockets is not installed')
class PassThroughHostTests(unittest.TestCase):
    """Sign-in gate on: the gateway insists on the browser's real Host/Origin (webapp.public_url), so they pass through."""

    @classmethod
    def setUpClass(cls):
        cls.port = free_port()
        cls.host = f'hermes.localhost:{cls.port}'
        cls.gateway = FakeGateway(public_host=cls.host)
        cls.gateway.start()
        policy = ProxyPolicy.from_environment({'ODYSSEUS_HERMES_PROXY_UPSTREAM_HOST': 'public'})
        cls.served = Served(HermesHostProxy(odysseus, f'http://127.0.0.1:{cls.gateway.port}', mode='gateway', policy=policy), cls.port)
        cls.served.__enter__()

    @classmethod
    def tearDownClass(cls):
        cls.served.__exit__()
        cls.gateway.shutdown()

    def setUp(self):
        self.gateway.requests.clear()

    def test_http_reaches_the_gateway_with_the_browsers_own_host_origin_and_referer(self):
        status, _, body = fetch(self.port, self.host, '/api/echo', headers={
            'Origin': f'http://{self.host}', 'Referer': f'http://{self.host}/page', 'Cookie': f'sess={TOKEN}'})
        data = json.loads(body)
        self.assertEqual(status, 200, body)
        self.assertEqual((data['host'], data['origin'], data['referer']), (self.host, f'http://{self.host}', f'http://{self.host}/page'))

    def test_websocket_reaches_the_gateway_with_the_browsers_own_host_and_origin(self):
        async def go():
            async with websockets.connect(f'ws://{self.host}/api/ws?token={TOKEN}', host='127.0.0.1', port=self.port,
                                          origin=f'http://{self.host}', subprotocols=['hermes.v1'], max_size=None) as ws:
                await ws.send('ping')
                self.assertEqual(await ws.recv(), 'ping')
                self.assertEqual(ws.subprotocol, 'hermes.v1')
        asyncio.run(go())
        seen = self.gateway.seen()[0]['headers']
        self.assertEqual((seen['host'], seen['origin']), (self.host, f'http://{self.host}'))

    def test_the_proxy_still_refuses_hostile_values_before_the_gateway(self):
        for origin in ('http://evil.test', 'null', f'http://127.0.0.1:{self.gateway.port}', f'http://hermes.localhost:{self.port + 1}'):
            with self.subTest(origin=origin):
                self.assertEqual(fetch(self.port, self.host, '/api/echo', 'POST', {'Origin': origin, 'Cookie': f'sess={TOKEN}'})[0], 403)
                status, _, sock = raw(self.port, handshake(self.host, origin=origin))
                sock.close()
                self.assertEqual(status, 403)
        for host in ('evil.test', f'127.0.0.1:{self.gateway.port}', f'hermes.localhost:{self.port + 1}'):
            self.assertEqual(fetch(self.port, host, '/api/echo', headers={'Cookie': f'sess={TOKEN}'})[2], b'ODYSSEUS')
        self.assertEqual(fetch(self.port, self.host, '/api/echo', headers={'X-Forwarded-For': '1.2.3.4'})[0], 403)
        self.assertEqual(self.gateway.seen(), [])

    def test_bound_mode_would_have_been_refused_by_this_gateway(self):
        port = free_port()
        with Served(HermesHostProxy(odysseus, f'http://127.0.0.1:{self.gateway.port}', mode='gateway'), port):
            self.assertEqual(fetch(port, f'hermes.localhost:{port}', '/api/echo', headers={'Cookie': f'sess={TOKEN}'})[0], 400)

    def test_setting_is_validated(self):
        self.assertFalse(ProxyPolicy.from_environment({}).pass_host)
        self.assertFalse(ProxyPolicy.from_environment({'ODYSSEUS_HERMES_PROXY_UPSTREAM_HOST': 'bound'}).pass_host)
        self.assertTrue(ProxyPolicy.from_environment({'ODYSSEUS_HERMES_PROXY_UPSTREAM_HOST': 'public'}).pass_host)
        for value in ('rewrite', 'PUBLIC', '1', 'true'):
            with self.subTest(value=value), self.assertRaises(ProxyConfigError):
                ProxyPolicy.from_environment({'ODYSSEUS_HERMES_PROXY_UPSTREAM_HOST': value})


class ExtraHostsAndOriginsTests(unittest.TestCase):
    def test_configured_names_and_origins_are_exact(self):
        policy = ProxyPolicy.from_environment({'ODYSSEUS_HERMES_PROXY_HOSTNAMES': 'projects.localhost',
                                               'ODYSSEUS_HERMES_PROXY_ORIGINS': 'http://tauri.localhost'})
        self.assertEqual(policy.hosts(7000), {'hermes.localhost:7000', 'projects.localhost:7000'})
        self.assertEqual(policy.origins(7000), {'http://hermes.localhost:7000', 'http://projects.localhost:7000', 'http://tauri.localhost'})

    def test_invalid_configuration_fails_closed(self):
        for key, value in (('ODYSSEUS_HERMES_PROXY_HOSTNAMES', 'evil.com'), ('ODYSSEUS_HERMES_PROXY_HOSTNAMES', '*.localhost'),
                           ('ODYSSEUS_HERMES_PROXY_HOSTNAMES', 'a b.localhost'), ('ODYSSEUS_HERMES_PROXY_ORIGINS', '*'),
                           ('ODYSSEUS_HERMES_PROXY_ORIGINS', 'http://evil.test/path'), ('ODYSSEUS_HERMES_PROXY_ORIGINS', 'null'),
                           ('ODYSSEUS_HERMES_PROXY_ORIGINS', 'file://'), ('ODYSSEUS_HERMES_PROXY_WS_MAX_MB', '0'),
                           ('ODYSSEUS_HERMES_PROXY_WS_MAX_MB', '99999'), ('ODYSSEUS_HERMES_PROXY_WS_MAX_MB', 'abc')):
            with self.subTest(key=key, value=value), self.assertRaises(ProxyConfigError):
                ProxyPolicy.from_environment({key: value})

    def test_install_uses_the_environment_and_fails_closed(self):
        class App:
            def __init__(self): self.added = []
            def add_middleware(self, cls, **kwargs): self.added.append((cls, kwargs))
        service = mock.Mock(proxy=False)
        service.config.url = 'http://127.0.0.1:9120'
        for env in ({'ODYSSEUS_HERMES_PROXY_MODE': 'nonsense'}, {'ODYSSEUS_HERMES_PROXY_HOSTNAMES': 'evil.com'}, {'ODYSSEUS_HERMES_PROXY': '0'}):
            app = App()
            self.assertFalse(install_hermes_proxy(app, service, env))
            self.assertEqual(app.added, [])
        app = App()
        self.assertTrue(install_hermes_proxy(app, service, {}))
        self.assertEqual(app.added[0][1]['mode'], 'webui')                          # default is the legacy behaviour
        app = App()
        self.assertTrue(install_hermes_proxy(app, service, {'ODYSSEUS_HERMES_PROXY_MODE': 'gateway'}))
        self.assertEqual(app.added[0][1]['mode'], 'gateway')

    def test_non_loopback_peer_is_refused(self):
        proxy = HermesHostProxy(odysseus, 'http://127.0.0.1:9120', mode='gateway')
        sent = []

        async def send(message): sent.append(message)
        async def receive(): return {'type': 'http.disconnect'}
        scope = {'type': 'http', 'method': 'GET', 'path': '/', 'headers': [(b'host', b'hermes.localhost:7000')],
                 'client': ('192.168.1.20', 5000), 'server': ('127.0.0.1', 7000)}
        asyncio.run(proxy(scope, receive, send))
        self.assertEqual(sent[0]['status'], 403)

    def test_extra_hostname_reaches_the_gateway(self):
        gateway = FakeGateway()
        gateway.start()
        port = free_port()
        policy = ProxyPolicy.from_environment({'ODYSSEUS_HERMES_PROXY_HOSTNAMES': 'projects.localhost'})
        with Served(HermesHostProxy(odysseus, f'http://127.0.0.1:{gateway.port}', mode='gateway', policy=policy), port):
            host = f'projects.localhost:{port}'
            self.assertEqual(fetch(port, host, '/api/echo', headers={'Origin': f'http://{host}', 'Cookie': f'sess={TOKEN}'})[0], 200)
            self.assertEqual(fetch(port, host, '/api/echo', 'POST', {'Origin': f'http://other.localhost:{port}'})[0], 403)
            self.assertEqual(fetch(port, f'other.localhost:{port}', '/api/echo')[2], b'ODYSSEUS')
        gateway.shutdown()


class LegacyWebuiModeUnchangedTests(GatewayProxyBase):
    mode = 'webui'

    def test_http_is_forwarded_as_before_with_host_and_origin_untouched(self):
        status, _, body = fetch(self.port, self.host, '/api/echo', headers={'Origin': 'http://evil.test', 'Cookie': f'sess={TOKEN}'})
        seen = self.gateway.seen()[0]['headers']
        self.assertEqual(seen['host'], self.host)                                  # no rewrite in webui mode
        self.assertEqual(seen['origin'], 'http://evil.test')                       # the WebUI judges Origin itself
        self.assertEqual(status, 400)                                              # fake gateway: its Host check refused the unrewritten Host

    def test_websockets_are_still_closed_with_1008(self):
        if websockets is None:
            self.skipTest('websockets is not installed')

        async def go():
            with self.assertRaises(Exception) as caught:
                async with websockets.connect(f'ws://hermes.localhost:{self.port}/api/ws', host='127.0.0.1', port=self.port,
                                              origin=f'http://{self.host}'):
                    pass
            return caught.exception
        asyncio.run(go())
        self.assertNoGatewayTraffic()


if __name__ == '__main__':
    unittest.main()

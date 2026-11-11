import asyncio
import gzip
import hashlib
import http.client
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import socket
import sys
import threading
import time
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

import uvicorn
from src.hermes_proxy import HermesHostProxy

GZIPPED = gzip.compress(b'compressed download ' * 200)
disconnected = threading.Event()


def free_port():
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        return sock.getsockname()[1]


class FakeHermes(BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'

    def log_message(self, *args):
        pass

    def reply(self, status, body=b'', headers=()):
        self.send_response(status)
        for name, value in headers:
            self.send_header(name, value)
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path.startswith('/echo'):
            body = json.dumps({'host': self.headers['Host'], 'origin': self.headers.get('Origin'), 'cookie': self.headers.get('Cookie'),
                               'forwarded': self.headers.get('X-Forwarded-For'), 'path': self.path}).encode()
            return self.reply(200, body, [('Content-Type', 'application/json'), ('Set-Cookie', 'a=1; Path=/; HttpOnly'), ('Set-Cookie', 'b=2; Path=/')])
        if self.path == '/redirect':
            return self.reply(302, b'', [('Location', f'http://127.0.0.1:{self.server.server_port}/login?next=%2F')])
        if self.path == '/gzip':
            return self.reply(200, GZIPPED, [('Content-Encoding', 'gzip'), ('Content-Type', 'text/plain')])
        if self.path == '/stream':
            self.send_response(200)
            self.send_header('Content-Type', 'text/event-stream')
            self.send_header('Cache-Control', 'no-cache')
            self.send_header('Connection', 'close')
            self.end_headers()
            try:
                if self.headers.get('X-Hold'):
                    for _ in range(100):
                        self.wfile.write(b': keepalive\n\n')
                        self.wfile.flush()
                        time.sleep(0.2)
                for index in range(5):
                    self.wfile.write(f'data: {index}\n\n'.encode())
                    self.wfile.flush()
                    time.sleep(0.3)
            except OSError:
                disconnected.set()
            self.close_connection = True
            return
        self.reply(404)

    def do_POST(self):
        digest, size = hashlib.sha256(), 0
        if self.headers.get('Transfer-Encoding', '').lower() == 'chunked':
            while True:
                length = int(self.rfile.readline().strip(), 16)
                if length == 0:
                    self.rfile.readline()
                    break
                digest.update(self.rfile.read(length))
                size += length
                self.rfile.readline()
        else:
            remaining = int(self.headers.get('Content-Length', 0))
            while remaining:
                chunk = self.rfile.read(min(65536, remaining))
                digest.update(chunk)
                size += len(chunk)
                remaining -= len(chunk)
        self.reply(200, json.dumps({'sha256': digest.hexdigest(), 'bytes': size, 'origin': self.headers.get('Origin')}).encode(), [('Content-Type', 'application/json')])


async def odysseus(scope, receive, send):
    if scope['type'] == 'http':
        await send({'type': 'http.response.start', 'status': 200, 'headers': [(b'content-type', b'text/plain')]})
        await send({'type': 'http.response.body', 'body': b'odysseus'})


class ProxyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.upstream_port = free_port()
        cls.start_upstream()
        cls.port = free_port()
        cls.app = HermesHostProxy(odysseus, f'http://127.0.0.1:{cls.upstream_port}/')
        cls.server = uvicorn.Server(uvicorn.Config(cls.app, host='127.0.0.1', port=cls.port, log_level='error', lifespan='off', loop='asyncio'))
        cls.thread = threading.Thread(target=cls.server.run, daemon=True)
        cls.thread.start()
        for _ in range(100):
            if cls.server.started:
                break
            time.sleep(0.05)

    @classmethod
    def start_upstream(cls):
        cls.hermes = ThreadingHTTPServer(('127.0.0.1', cls.upstream_port), FakeHermes)
        cls.hermes.daemon_threads = True
        threading.Thread(target=cls.hermes.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.server.should_exit = True
        cls.thread.join(5)
        cls.hermes.shutdown()
        cls.hermes.server_close()

    def call(self, method='GET', path='/', host=None, headers=None, body=None, encode_chunked=False):
        connection = http.client.HTTPConnection('127.0.0.1', self.port, timeout=15)
        merged = {'Host': host or f'hermes.localhost:{self.port}', **(headers or {})}
        connection.request(method, path, body=body, headers=merged, encode_chunked=encode_chunked)
        response = connection.getresponse()
        return connection, response

    def test_other_hosts_and_lookalikes_reach_odysseus(self):
        for host in (f'localhost:{self.port}', f'127.0.0.1:{self.port}', f'hermes.localhost.evil.test:{self.port}',
                     f'evilhermes.localhost:{self.port}', 'hermes.localhost:9', 'hermes.localhost'):
            connection, response = self.call(host=host)
            self.assertEqual(response.read(), b'odysseus', host)
            connection.close()

    def test_headers_cookies_query_and_origin_pass_through_unchanged(self):
        origin = f'http://hermes.localhost:{self.port}'
        connection, response = self.call(path='/echo?x=1&y=%2F', headers={'Origin': origin, 'Cookie': 'hermes_session=[REDACTED]'})
        data = json.loads(response.read())
        self.assertEqual((data['host'], data['origin'], data['cookie'], data['path']),
                         (f'hermes.localhost:{self.port}', origin, 'hermes_session=[REDACTED]', '/echo?x=1&y=%2F'))
        self.assertEqual([v for k, v in response.getheaders() if k.lower() == 'set-cookie'], ['a=1; Path=/; HttpOnly', 'b=2; Path=/'])
        connection.close()

    def test_the_proxy_never_remembers_or_replays_cookies(self):
        origin = f'http://hermes.localhost:{self.port}'
        first = self.call(path='/echo', headers={'Origin': origin})
        self.assertEqual(len([k for k, _ in first[1].getheaders() if k.lower() == 'set-cookie']), 2)
        first[1].read(); first[0].close()
        for _ in range(3):
            connection, response = self.call(path='/echo', headers={'Origin': origin})
            self.assertIsNone(json.loads(response.read())['cookie'], 'a cookie Hermes set for one client reached a later client')
            connection.close()
        connection, response = self.call(path='/echo', headers={'Cookie': 'mine=1'})
        self.assertEqual(json.loads(response.read())['cookie'], 'mine=1')
        connection.close()

    def test_redirect_to_the_internal_address_is_rewritten_to_the_public_host(self):
        connection, response = self.call(path='/redirect')
        self.assertEqual(response.status, 302)
        self.assertEqual(response.getheader('Location'), f'http://hermes.localhost:{self.port}/login?next=%2F')
        connection.close()

    def test_compressed_downloads_stay_byte_identical(self):
        connection, response = self.call(path='/gzip', headers={'Accept-Encoding': 'gzip'})
        self.assertEqual(response.getheader('Content-Encoding'), 'gzip')
        self.assertEqual(response.read(), GZIPPED)
        connection.close()

    def test_sse_is_streamed_not_buffered(self):
        started = time.monotonic()
        connection, response = self.call(path='/stream')
        first = response.readline()
        self.assertEqual(first, b'data: 0\n')
        self.assertLess(time.monotonic() - started, 0.9)
        rest = response.read()
        self.assertIn(b'data: 4', rest)
        self.assertGreater(time.monotonic() - started, 1.0)
        connection.close()

    def test_large_uploads_content_length_and_chunked(self):
        payload = bytes(range(256)) * 12000
        expected = hashlib.sha256(payload).hexdigest()
        origin = f'http://hermes.localhost:{self.port}'
        connection, response = self.call('POST', '/upload', headers={'Origin': origin, 'Content-Type': 'application/octet-stream'}, body=payload)
        result = json.loads(response.read())
        self.assertEqual((result['sha256'], result['bytes'], result['origin']), (expected, len(payload), origin))
        connection.close()
        pieces = (payload[i:i + 50000] for i in range(0, len(payload), 50000))
        connection, response = self.call('POST', '/upload', headers={'Content-Type': 'application/octet-stream'}, body=pieces, encode_chunked=True)
        self.assertEqual(json.loads(response.read())['sha256'], expected)
        connection.close()

    def test_forwarded_headers_are_refused_and_never_reach_hermes(self):
        for name in ('X-Forwarded-For', 'X-Forwarded-Host', 'Forwarded', 'X-Real-IP'):
            connection, response = self.call(path='/echo', headers={name: '203.0.113.9'})
            self.assertEqual(response.status, 403, name)
            self.assertEqual(response.getheader('X-Frame-Options'), 'DENY')
            connection.close()

    def test_non_loopback_peers_and_websockets_are_refused_without_contacting_hermes(self):
        sent = []

        async def send(message):
            sent.append(message)

        async def receive():
            return {'type': 'websocket.connect'}

        scope = {'type': 'http', 'method': 'GET', 'path': '/', 'raw_path': b'/', 'query_string': b'', 'client': ('192.0.2.4', 5000),
                 'server': ('0.0.0.0', self.port), 'headers': [(b'host', f'hermes.localhost:{self.port}'.encode())]}
        asyncio.run(self.app(scope, receive, send))
        self.assertEqual(sent[0]['status'], 403)
        sent.clear()
        asyncio.run(self.app({**scope, 'type': 'websocket', 'client': ('127.0.0.1', 5000)}, receive, send))
        self.assertEqual(sent, [{'type': 'websocket.close', 'code': 1008}])

    def test_client_disconnect_closes_the_upstream_stream(self):
        disconnected.clear()
        connection, response = self.call(path='/stream', headers={'X-Hold': '1'})
        response.readline()
        connection.sock.shutdown(socket.SHUT_RDWR)
        response.close()
        connection.close()
        self.assertTrue(disconnected.wait(10), 'upstream stream was left open after the browser went away')

    def test_backend_down_is_reported_and_recovery_needs_no_proxy_restart(self):
        self.hermes.shutdown()
        self.hermes.server_close()
        connection, response = self.call(path='/echo')
        self.assertEqual(response.status, 502)
        self.assertIn(b'Hermes is not running', response.read())
        connection.close()
        connection, response = self.call(host=f'localhost:{self.port}')
        self.assertEqual(response.read(), b'odysseus')
        connection.close()
        type(self).start_upstream()
        connection, response = self.call(path='/echo')
        self.assertEqual(response.status, 200)
        connection.close()

    def test_only_a_loopback_upstream_is_accepted(self):
        for url in ('http://192.168.1.5:8787/', 'http://localhost:8787/', 'https://127.0.0.1:8787/', 'http://127.0.0.1/'):
            with self.assertRaises(ValueError):
                HermesHostProxy(odysseus, url)


if __name__ == '__main__':
    unittest.main()

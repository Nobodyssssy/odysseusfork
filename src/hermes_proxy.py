"""Host-routed loopback proxy: hermes.localhost:<port> -> the independent Hermes backend.

Requests whose Host is exactly hermes.localhost:<listener port> are forwarded to
Hermes with Host, Origin, bodies and the caller's own Cookie header unchanged, so
Hermes keeps its own origin, CSRF and CSP; Hermes' Set-Cookie goes back to the
browser. The proxy itself keeps no cookies: its shared HTTP client would otherwise
store a Hermes login and attach it to later requests from any local process.
Every other request reaches Odysseus unchanged. This is not a security boundary
beyond the checks below; Hermes keeps its own auth.
"""
import asyncio
import socket
import time
from urllib.parse import urlsplit

import httpx

PROXY_HOST = 'hermes.localhost'
_LOOPBACK = {'127.0.0.1', '::1'}
_HOP_BY_HOP = {b'connection', b'keep-alive', b'proxy-authenticate', b'proxy-authorization',
               b'proxy-connection', b'te', b'trailer', b'transfer-encoding', b'upgrade'}
_FORWARDED = {b'forwarded', b'x-forwarded-for', b'x-forwarded-host', b'x-forwarded-proto',
              b'x-forwarded-port', b'x-real-ip', b'x-real-host'}
_ERROR_HEADERS = [(b'content-type', b'text/plain; charset=utf-8'), (b'cache-control', b'no-store'),
                  (b'x-content-type-options', b'nosniff'), (b'x-frame-options', b'DENY'),
                  (b'content-security-policy', b"default-src 'none'; frame-ancestors 'none'")]


class _NoCookies(httpx.Cookies):
    """An always-empty jar: nothing is stored from responses and nothing is added to requests."""

    def extract_cookies(self, response):
        return None

    def set_cookie_header(self, request):
        return None

    def set(self, *args, **kwargs):
        return None


class _StatelessClient(httpx.AsyncClient):
    @property
    def cookies(self):
        return _NoCookies()

    @cookies.setter
    def cookies(self, cookies):
        return None


class HermesHostProxy:
    def __init__(self, app, upstream):
        parts = urlsplit(upstream)
        if parts.scheme != 'http' or parts.hostname != '127.0.0.1' or not parts.port:
            raise ValueError('The Hermes upstream must be an explicit http://127.0.0.1:PORT/ address')
        self.app = app
        self.upstream = f'http://127.0.0.1:{parts.port}'
        self._client = None

    def _http(self):
        if self._client is None:
            self._client = _StatelessClient(
                base_url=self.upstream, follow_redirects=False, trust_env=False,
                timeout=httpx.Timeout(connect=5, read=None, write=120, pool=10),
                limits=httpx.Limits(max_connections=64, max_keepalive_connections=8))
        return self._client

    @staticmethod
    def _host(scope):
        for name, value in scope['headers']:
            if name == b'host':
                return value.decode('latin-1').lower()
        return ''

    def _matches(self, scope):
        port = (scope.get('server') or (None, None))[1]
        allowed = {f'{PROXY_HOST}:{port}'} if port else set()
        if port == 80:
            allowed.add(PROXY_HOST)
        return self._host(scope) in allowed

    @staticmethod
    def _permitted(scope):
        client = (scope.get('client') or (None,))[0]
        return client in _LOOPBACK and not any(name in _FORWARDED for name, _ in scope['headers'])

    async def __call__(self, scope, receive, send):
        if scope['type'] not in ('http', 'websocket') or not self._matches(scope):
            return await self.app(scope, receive, send)
        if scope['type'] == 'websocket':
            await receive()
            return await send({'type': 'websocket.close', 'code': 1008})
        if not self._permitted(scope):
            return await self._fail(send, 403, b'Hermes is available only from this computer.')
        await self._forward(scope, receive, send)

    @staticmethod
    async def _fail(send, status, message):
        await send({'type': 'http.response.start', 'status': status,
                    'headers': _ERROR_HEADERS + [(b'content-length', str(len(message)).encode())]})
        await send({'type': 'http.response.body', 'body': message})

    async def _forward(self, scope, receive, send):
        headers = [(n, v) for n, v in scope['headers'] if n not in _HOP_BY_HOP]
        names = {n for n, _ in headers}
        has_body = b'transfer-encoding' in dict(scope['headers']) or (b'content-length' in names and dict(headers)[b'content-length'] != b'0')

        async def body():
            while True:
                message = await receive()
                if message['type'] == 'http.disconnect':
                    raise ConnectionAbortedError('client disconnected')
                if message.get('body'):
                    yield message['body']
                if not message.get('more_body'):
                    return

        target = scope['raw_path'].decode('latin-1') if scope.get('raw_path') else scope['path']
        if scope.get('query_string'):
            target += '?' + scope['query_string'].decode('latin-1')
        request = self._http().build_request(scope['method'], target, headers=headers, content=body() if has_body else None)
        try:
            upstream = await self._http().send(request, stream=True)
        except ConnectionAbortedError:
            return
        except (httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout):
            return await self._fail(send, 502, b'Hermes is not running. Open Odysseus and select Hermes Mode to start it.')
        except httpx.HTTPError:
            return await self._fail(send, 502, b'Hermes returned an invalid response.')

        async def watch_disconnect():
            while (await receive())['type'] != 'http.disconnect':
                pass

        watcher = asyncio.ensure_future(watch_disconnect())
        try:
            own_origin = 'http://' + self._host(scope)
            response_headers = []
            for name, value in upstream.headers.raw:
                lowered = name.lower()
                if lowered in _HOP_BY_HOP:
                    continue
                if lowered == b'location' and value.startswith(self.upstream.encode()):
                    value = own_origin.encode() + value[len(self.upstream):]
                response_headers.append((lowered, value))
            await send({'type': 'http.response.start', 'status': upstream.status_code, 'headers': response_headers})
            if scope['method'] == 'HEAD':
                return await send({'type': 'http.response.body', 'body': b''})
            iterator = upstream.aiter_raw().__aiter__()
            while True:
                chunk_task = asyncio.ensure_future(iterator.__anext__())
                await asyncio.wait({chunk_task, watcher}, return_when=asyncio.FIRST_COMPLETED)
                if not chunk_task.done():
                    chunk_task.cancel()
                    return
                try:
                    chunk = chunk_task.result()
                except StopAsyncIteration:
                    break
                await send({'type': 'http.response.body', 'body': chunk, 'more_body': True})
            await send({'type': 'http.response.body', 'body': b''})
        finally:
            watcher.cancel()
            await upstream.aclose()


_resolution = {'ok': None, 'checked': 0.0}


def hostname_resolves():
    """Whether this OS resolver maps hermes.localhost to loopback; cached briefly."""
    now = time.monotonic()
    if _resolution['ok'] is None or (not _resolution['ok'] and now - _resolution['checked'] > 30):
        try:
            _resolution['ok'] = any(info[4][0] in _LOOPBACK for info in socket.getaddrinfo(PROXY_HOST, None))
        except OSError:
            _resolution['ok'] = False
        _resolution['checked'] = now
    return _resolution['ok']


def install_hermes_proxy(app, service, environment=None):
    """Install as the outermost middleware so Odysseus auth, CSP and compression never touch Hermes traffic."""
    import os
    source = os.environ if environment is None else environment
    if service.config is None or source.get('ODYSSEUS_HERMES_PROXY') == '0':
        return False
    app.add_middleware(HermesHostProxy, upstream=service.config.url)
    service.proxy = True
    return True

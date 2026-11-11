"""Host-routed loopback proxy: hermes.localhost:<port> -> an independent Hermes backend.

Requests whose Host is exactly hermes.localhost:<listener port> are forwarded to the Hermes backend; every other
request reaches Odysseus unchanged. Two backends are supported, chosen by ODYSSEUS_HERMES_PROXY_MODE (default
``webui``, so nothing changes until the launcher opts in):

``webui``    the standalone Hermes WebUI. Host, Origin, bodies and the caller's own Cookie header are forwarded
             unchanged, so the WebUI keeps its own origin/CSRF/CSP; WebSockets are refused (1008) as before.
``gateway``  the official Hermes Gateway/Webapp. The gateway accepts only its own loopback authority as Host and
             Origin (DNS-rebinding defence, ``hermes_cli.web_server_boundary``) and documents a proxy that rewrites
             Host to the backend's as the supported arrangement. So this proxy first applies its OWN strict Host/Origin
             policy to what the browser sent (the browser only ever talks to the proxy) and only then presents the
             gateway's bound authority upstream. The gateway's checks stay fully active and its session-token
             authentication is untouched: this module never adds, stores or replays a credential.
             WebSockets are relayed frame for frame.

Both modes: the peer must be loopback and no Forwarded/X-Forwarded-*/X-Real-* header may be present.
Additionally in ``gateway`` mode (a refusal never reaches Hermes):
  * exactly one Host header, equal to an allowed ``<name>.localhost:<port>`` (checked before routing);
  * at most one Origin header; when present it must be exactly an allowed origin (``Origin: null`` never is);
  * Sec-Fetch-Site, when present: WebSockets must be ``same-origin``; state-changing HTTP methods must be
    ``same-origin`` or ``none``;
  * WebSocket upgrades must carry an allowed Origin (browsers always send one) unless explicitly relaxed;
  * Hermes' CORS response headers are dropped: this proxy grants no cross-origin access.

The proxy keeps no cookies: its shared HTTP client would otherwise store a Hermes login and attach it to later
requests from any local process. This is not a security boundary beyond the checks above; Hermes keeps its own auth.
"""
import asyncio
from dataclasses import dataclass
import logging
import re
import socket
import time
from urllib.parse import urlsplit

import httpx

PROXY_HOST = 'hermes.localhost'
MODES = ('webui', 'gateway')
DEFAULT_WS_MAX_MB = 400            # the gateway accepts one 384 MiB frame for a large attachment (web_server_boundary)
_LOOPBACK = {'127.0.0.1', '::1'}
_HOP_BY_HOP = {b'connection', b'keep-alive', b'proxy-authenticate', b'proxy-authorization',
               b'proxy-connection', b'te', b'trailer', b'transfer-encoding', b'upgrade'}
_FORWARDED = {b'forwarded', b'x-forwarded-for', b'x-forwarded-host', b'x-forwarded-proto',
              b'x-forwarded-port', b'x-real-ip', b'x-real-host'}
_WS_HANDSHAKE = {b'host', b'origin', b'upgrade', b'connection', b'content-length', b'sec-websocket-key',
                 b'sec-websocket-version', b'sec-websocket-extensions', b'sec-websocket-protocol', b'sec-websocket-accept'}
_ERROR_HEADERS = [(b'content-type', b'text/plain; charset=utf-8'), (b'cache-control', b'no-store'),
                  (b'x-content-type-options', b'nosniff'), (b'x-frame-options', b'DENY'),
                  (b'content-security-policy', b"default-src 'none'; frame-ancestors 'none'")]
_SAFE_METHODS = {'GET', 'HEAD', 'OPTIONS'}
_LABEL = r'[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?'
_NAME = re.compile(rf'^(?:{_LABEL}\.)*localhost$')
_ORIGIN = re.compile(rf'^https?://(?:{_LABEL}\.)*{_LABEL}(?::[0-9]{{1,5}})?$')
_RESERVED_CLOSE = {1005, 1006, 1015}
_SECRET_QUERY = re.compile(r'([?&](?:token|ticket|access_token|session_token|code|launch)=)[^&\s"\']+', re.I)
log = logging.getLogger('odysseus.hermes_proxy')


class RedactSecrets(logging.Filter):
    """Blank credential-looking query parameters in log lines. httpx and uvicorn log full request URLs at INFO, and a
    one-use ticket or session token must not outlive its use in a log file."""

    def filter(self, record):
        try:
            if isinstance(record.msg, str):
                record.msg = _SECRET_QUERY.sub(r'\1[redacted]', record.msg)
            if isinstance(record.args, tuple):
                record.args = tuple(_SECRET_QUERY.sub(r'\1[redacted]', arg) if isinstance(arg, str) else arg for arg in record.args)
        except Exception:                      # a logging helper must never break the request being logged
            pass
        return True


def redact_request_logs():
    for name in ('httpx', 'uvicorn.access'):
        target = logging.getLogger(name)
        if not any(isinstance(item, RedactSecrets) for item in target.filters):
            target.addFilter(RedactSecrets())


class ProxyConfigError(ValueError):
    """The proxy configuration is invalid; the proxy is not installed (fail closed)."""


def _csv(value):
    return [item.strip() for item in (value or '').split(',') if item.strip()]


@dataclass(frozen=True)
class ProxyPolicy:
    """Which Host and Origin values the gateway-mode proxy accepts. Exact matches only, no wildcards."""
    names: frozenset = frozenset({PROXY_HOST})
    extra_origins: frozenset = frozenset()
    allow_missing_origin_http: bool = True       # plain navigations and curl-style clients send none
    allow_missing_origin_ws: bool = False        # browsers always send one; a native client should dial the gateway itself
    ws_max_bytes: int = DEFAULT_WS_MAX_MB * 1024 * 1024
    pass_host: bool = False                      # False: present the gateway's own authority upstream; True: the browser's Host/Origin

    def hosts(self, port):
        accepted = {f'{name}:{port}' for name in self.names}
        if port == 80:
            accepted |= set(self.names)
        return accepted

    def origins(self, port):
        return {f'http://{host}' for host in self.hosts(port)} | set(self.extra_origins)

    @classmethod
    def from_environment(cls, environment):
        """ODYSSEUS_HERMES_PROXY_HOSTNAMES        extra names, each ``localhost`` or ``*.localhost`` (browsers resolve them to loopback)
           ODYSSEUS_HERMES_PROXY_ORIGINS          extra exact origins, e.g. ``http://projects.localhost:7000``
           ODYSSEUS_HERMES_PROXY_WS_NO_ORIGIN=1   accept WebSocket upgrades that carry no Origin header
           ODYSSEUS_HERMES_PROXY_WS_MAX_MB        largest single WebSocket frame, 1-1024 (default 400)
           ODYSSEUS_HERMES_PROXY_UPSTREAM_HOST    ``bound`` (default): present the gateway's own 127.0.0.1:PORT as Host/Origin; use with
                                                  webapp.public_url unset. ``public``: pass the browser's Host/Origin/Referer through
                                                  unchanged; required when the gateway's sign-in gate is on, because it then insists on
                                                  the real origin (webapp.public_url must be http://hermes.localhost:<Odysseus port>)"""
        names = {PROXY_HOST}
        for name in _csv(environment.get('ODYSSEUS_HERMES_PROXY_HOSTNAMES')):
            if not _NAME.match(name) or len(name) > 253:
                raise ProxyConfigError(f'ODYSSEUS_HERMES_PROXY_HOSTNAMES: {name!r} is not localhost or a *.localhost name')
            names.add(name)
        origins = set()
        for origin in _csv(environment.get('ODYSSEUS_HERMES_PROXY_ORIGINS')):
            if not _ORIGIN.match(origin):
                raise ProxyConfigError(f'ODYSSEUS_HERMES_PROXY_ORIGINS: {origin!r} is not an exact http(s) origin')
            origins.add(origin)
        megabytes = environment.get('ODYSSEUS_HERMES_PROXY_WS_MAX_MB') or str(DEFAULT_WS_MAX_MB)
        if not megabytes.isdigit() or not 1 <= int(megabytes) <= 1024:
            raise ProxyConfigError('ODYSSEUS_HERMES_PROXY_WS_MAX_MB must be a whole number from 1 to 1024')
        upstream_host = environment.get('ODYSSEUS_HERMES_PROXY_UPSTREAM_HOST') or 'bound'
        if upstream_host not in ('bound', 'public'):
            raise ProxyConfigError("ODYSSEUS_HERMES_PROXY_UPSTREAM_HOST must be 'bound' or 'public'")
        return cls(names=frozenset(names), extra_origins=frozenset(origins),
                   allow_missing_origin_ws=environment.get('ODYSSEUS_HERMES_PROXY_WS_NO_ORIGIN') == '1',
                   ws_max_bytes=int(megabytes) * 1024 * 1024, pass_host=upstream_host == 'public')


UVICORN_DEFAULT_WS_MAX = 16 * 1024 * 1024


def uvicorn_ws_max_size(environment=None):
    """uvicorn ``ws_max_size`` for the Odysseus server: unchanged unless the gateway proxy mode is on, because a large
    attachment arrives as one browser frame and must fit through the relay."""
    import os
    source = os.environ if environment is None else environment
    if (source.get('ODYSSEUS_HERMES_PROXY_MODE') or 'webui') != 'gateway' or source.get('ODYSSEUS_HERMES_PROXY') == '0':
        return UVICORN_DEFAULT_WS_MAX
    try:
        return ProxyPolicy.from_environment(source).ws_max_bytes
    except ProxyConfigError:
        return UVICORN_DEFAULT_WS_MAX


def _websockets():
    try:
        from websockets.asyncio.client import connect
        import websockets.exceptions as errors
    except ImportError:
        return None
    return connect, errors


def _close_code(code):
    if code is None or code == 1005:
        return 1000
    if code in _RESERVED_CLOSE or not 1000 <= code <= 4999:
        return 1011
    return code


def _close_reason(reason):
    return (reason or '').encode('utf-8')[:120].decode('utf-8', 'ignore')


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
    def __init__(self, app, upstream, mode='webui', policy=None):
        parts = urlsplit(upstream)
        if parts.scheme != 'http' or parts.hostname != '127.0.0.1' or not parts.port:
            raise ValueError('The Hermes upstream must be an explicit http://127.0.0.1:PORT/ address')
        if mode not in MODES:
            raise ProxyConfigError(f'The Hermes proxy mode must be one of {MODES}')
        self.app = app
        self.upstream = f'http://127.0.0.1:{parts.port}'
        self.authority = f'127.0.0.1:{parts.port}'
        self.mode = mode
        self.policy = policy or ProxyPolicy()
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

    @staticmethod
    def _port(scope):
        return (scope.get('server') or (None, None))[1]

    def _matches(self, scope):
        port = self._port(scope)
        if not port:
            return False
        if self.mode == 'gateway':
            return self._host(scope) in self.policy.hosts(port)
        return self._host(scope) in ({f'{PROXY_HOST}:{port}'} | ({PROXY_HOST} if port == 80 else set()))

    @staticmethod
    def _permitted(scope):
        client = (scope.get('client') or (None,))[0]
        return client in _LOOPBACK and not any(name in _FORWARDED for name, _ in scope['headers'])

    def _refuse(self, scope, *, websocket):
        """None when the request may be forwarded, else (status, message). Nothing here reaches Hermes."""
        if not self._permitted(scope):
            return 403, b'Hermes is available only from this computer.'
        if self.mode != 'gateway':
            return None
        headers = scope['headers']
        if sum(1 for name, _ in headers if name == b'host') != 1:
            return 400, b'Ambiguous Host header.'
        origins = [value for name, value in headers if name == b'origin']
        if len(origins) > 1:
            return 400, b'Ambiguous Origin header.'
        if origins:
            if origins[0].decode('latin-1') not in self.policy.origins(self._port(scope)):
                return 403, b'Origin not allowed.'
        elif websocket and not self.policy.allow_missing_origin_ws:
            return 403, b'Origin required.'
        elif not websocket and not self.policy.allow_missing_origin_http:
            return 403, b'Origin required.'
        site = next((value.decode('latin-1').lower() for name, value in headers if name == b'sec-fetch-site'), None)
        if site is not None:
            if websocket and site != 'same-origin':
                return 403, b'Cross-site WebSocket refused.'
            if not websocket and scope['method'] not in _SAFE_METHODS and site not in ('same-origin', 'none'):
                return 403, b'Cross-site request refused.'
        return None

    async def __call__(self, scope, receive, send):
        if scope['type'] not in ('http', 'websocket') or not self._matches(scope):
            return await self.app(scope, receive, send)
        if scope['type'] == 'websocket':
            if self.mode != 'gateway':
                await receive()
                return await send({'type': 'websocket.close', 'code': 1008})
            return await self._websocket(scope, receive, send)
        refusal = self._refuse(scope, websocket=False)
        if refusal:
            log.warning('refused %s %s: %s', scope['method'], scope['path'], refusal[1].decode())
            return await self._fail(send, *refusal)
        await self._forward(scope, receive, send)

    @staticmethod
    async def _fail(send, status, message):
        await send({'type': 'http.response.start', 'status': status,
                    'headers': _ERROR_HEADERS + [(b'content-length', str(len(message)).encode())]})
        await send({'type': 'http.response.body', 'body': message})

    def _toward_upstream(self, scope, headers):
        """gateway mode: present the gateway's own bound authority (the browser's values were already validated).
        With pass_host the gateway sees the browser's own Host/Origin/Referer, so its own checks judge the real values."""
        if self.mode != 'gateway' or self.policy.pass_host:
            return headers
        own = ('http://' + self._host(scope)).encode()
        rewritten = []
        for name, value in headers:
            if name == b'host':
                value = self.authority.encode()
            elif name == b'origin':
                value = self.upstream.encode()
            elif name == b'referer' and value.startswith(own):
                value = self.upstream.encode() + value[len(own):]
            rewritten.append((name, value))
        return rewritten

    async def _forward(self, scope, receive, send):
        headers = self._toward_upstream(scope, [(n, v) for n, v in scope['headers'] if n not in _HOP_BY_HOP])
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
                if self.mode == 'gateway' and lowered.startswith(b'access-control-'):
                    continue                         # same-origin only: this proxy grants no cross-origin access
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

    # ── WebSocket relay (gateway mode) ──────────────────────────────────────────────────────────
    @staticmethod
    async def _ws_refuse(scope, send, status, message):
        if 'websocket.http.response' in (scope.get('extensions') or {}):
            await send({'type': 'websocket.http.response.start', 'status': status,
                        'headers': _ERROR_HEADERS + [(b'content-length', str(len(message)).encode())]})
            return await send({'type': 'websocket.http.response.body', 'body': message})
        await send({'type': 'websocket.close', 'code': 1008})

    async def _websocket(self, scope, receive, send):
        first = await receive()
        if first['type'] != 'websocket.connect':
            return
        refusal = self._refuse(scope, websocket=True)
        if refusal:
            log.warning('refused WebSocket %s: %s', scope['path'], refusal[1].decode())
            return await self._ws_refuse(scope, send, *refusal)
        client = _websockets()
        if client is None:
            log.error('WebSocket support (the websockets package) is not installed; refusing %s', scope['path'])
            return await self._ws_refuse(scope, send, 501, b'WebSocket support is not installed.')
        connect, errors = client
        target = scope['raw_path'].decode('latin-1') if scope.get('raw_path') else scope['path']
        if scope.get('query_string'):
            target += '?' + scope['query_string'].decode('latin-1')
        forwarded = [(n.decode('latin-1'), v.decode('latin-1')) for n, v in scope['headers']
                     if n not in _WS_HANDSHAKE and n not in _HOP_BY_HOP]
        browser_origin = next((v.decode('latin-1') for n, v in scope['headers'] if n == b'origin'), None)
        if self.policy.pass_host:
            authority, origin, where = self._host(scope), browser_origin, {'host': '127.0.0.1', 'port': int(self.authority.rsplit(':', 1)[1])}
        else:
            authority, origin, where = self.authority, (self.upstream if browser_origin is not None else None), {}
        try:
            upstream = await connect(
                f'ws://{authority}{target}', additional_headers=forwarded, origin=origin,
                subprotocols=scope.get('subprotocols') or None, compression=None, max_size=self.policy.ws_max_bytes,
                ping_interval=None, open_timeout=10, close_timeout=5, user_agent_header=None, **where)
        except errors.InvalidStatus as exc:
            body = (exc.response.body or b'')[:2000] or b'Hermes refused the connection.'
            return await self._ws_refuse(scope, send, exc.response.status_code, body)
        except (OSError, asyncio.TimeoutError, errors.WebSocketException):
            return await self._ws_refuse(scope, send, 502, b'Hermes is not running. Open Odysseus and select Hermes Mode to start it.')
        await send({'type': 'websocket.accept', 'subprotocol': upstream.subprotocol})

        async def to_upstream():
            while True:
                event = await receive()
                if event['type'] == 'websocket.disconnect':
                    return 'client', event.get('code', 1005)
                if event['type'] != 'websocket.receive':
                    continue
                if event.get('text') is not None:
                    await upstream.send(event['text'])
                elif event.get('bytes') is not None:
                    await upstream.send(event['bytes'])

        async def to_client():
            try:
                while True:
                    message = await upstream.recv()
                    if isinstance(message, str):
                        await send({'type': 'websocket.send', 'text': message})
                    else:
                        await send({'type': 'websocket.send', 'bytes': bytes(message)})
            except errors.ConnectionClosed:
                return 'upstream', None

        tasks = {asyncio.ensure_future(to_upstream()), asyncio.ensure_future(to_client())}
        side, code = 'error', 1011
        try:
            done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            for task in pending:
                task.cancel()
            await asyncio.gather(*pending, return_exceptions=True)
            for task in done:
                if task.exception() is None:
                    side, code = task.result()
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            try:
                if side == 'client':
                    await upstream.close(_close_code(code))
                elif side == 'upstream':
                    reason = _close_reason(upstream.close_reason)
                    await send({'type': 'websocket.close', 'code': _close_code(upstream.close_code), 'reason': reason})
                else:
                    await upstream.close(1011)
                    await send({'type': 'websocket.close', 'code': 1011})
            except Exception:                      # the other side is already gone; nothing more to tell it
                log.debug('WebSocket close relay failed', exc_info=True)
            finally:
                await upstream.close()


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
    mode = source.get('ODYSSEUS_HERMES_PROXY_MODE') or 'webui'
    try:
        if mode not in MODES:
            raise ProxyConfigError(f'ODYSSEUS_HERMES_PROXY_MODE must be one of {MODES}')
        policy = ProxyPolicy.from_environment(source)
    except ProxyConfigError as exc:
        log.error('Hermes proxy not installed: %s', exc)
        return False
    app.add_middleware(HermesHostProxy, upstream=service.config.url, mode=mode, policy=policy)
    if mode == 'gateway':
        redact_request_logs()
    service.proxy = True
    return True

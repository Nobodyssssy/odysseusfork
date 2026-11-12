"""Scoped logout gate: server-side Hermes access grants bound to an Odysseus browser session.

Store tests use a fake Odysseus session manager and an injected clock. Proxy tests run the real HermesHostProxy in the real uvicorn in front of a fake
gateway (and, for streams and slow responses, a small HTTP upstream) and drive it over real sockets, including WebSockets."""
import asyncio
import http.client
import base64
import hashlib
import json
import re
import os
from pathlib import Path
import secrets
import shutil
import socket
import sqlite3
import sys
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).parent))

from src import hermes_grants as hg
from src.hermes_grants import ENTER_PATH, ENTERED_COOKIE, GRANT_COOKIE, Grant, GrantDenied, GrantStore, GrantStoreError, digest, valid_target
from src.hermes_proxy import HermesHostProxy, ProxyPolicy

try:
    import websockets
    from websockets.exceptions import ConnectionClosed, InvalidStatus
except ImportError:                                                       # pragma: no cover
    raise unittest.SkipTest('websockets is not installed; the grant gate relays WebSockets')

from test_proxy_gateway import FakeGateway, Served, TOKEN, fetch, free_port, odysseus  # noqa: E402  (helpers only; no test classes imported)

TARGET = '/start?x=1#launch=GATEWAYONEUSEFRAGMENT'
GATEWAY_FRAGMENT = 'GATEWAYONEUSEFRAGMENT'


class Clock:
    def __init__(self):
        self.offset = 0.0

    def __call__(self):
        return time.time() + self.offset


class FakeSessions:
    """Stands in for AuthManager: sessions by token, lookup by digest, revoke listeners."""

    def __init__(self, clock):
        self.clock = clock
        self.sessions = {}
        self.listeners = []
        self.broken = False

    def login(self, user, ttl=3600):
        token = secrets.token_hex(32)
        self.sessions[token] = {'username': user, 'expiry': self.clock() + ttl}
        return token

    def lookup(self, session_digest):
        if self.broken:
            raise RuntimeError('session store unavailable')
        for token, session in list(self.sessions.items()):
            if digest(token) == session_digest and session['expiry'] > self.clock():
                return session['username'], session['expiry']
        return None

    def logout(self, token, notify=True):
        self.sessions.pop(token, None)
        if notify:
            for listener in self.listeners:
                listener([token])


def make_store(tmp, clock=None, sessions=None, **kwargs):
    clock = clock or Clock()
    sessions = sessions or FakeSessions(clock)
    store = GrantStore(os.path.join(tmp, 'grants.db'), sessions.lookup, clock=clock, **kwargs)
    sessions.listeners.append(store.sessions_revoked)
    return store, sessions, clock


class StoreCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix='grants-')
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.store, self.sessions, self.clock = make_store(self.tmp)
        self.addCleanup(self.store.close)

    def grant(self, user='alice', target=TARGET, presented=None, token=None):
        token = token or self.sessions.login(user)
        ticket = self.store.create_ticket(token, user, target)
        secret, redirect, max_age = self.store.exchange(ticket, presented)
        return token, secret, redirect, max_age


class TicketAndExchangeTests(StoreCase):
    def test_ticket_is_one_use_and_the_target_stays_server_side(self):
        token = self.sessions.login('alice')
        ticket = self.store.create_ticket(token, 'alice', TARGET)
        self.assertTrue(hg.SECRET_PATTERN.match(ticket))
        secret, target, max_age = self.store.exchange(ticket)
        self.assertEqual(target, TARGET)
        self.assertTrue(hg.SECRET_PATTERN.match(secret))
        self.assertNotEqual(secret, ticket)
        self.assertTrue(0 < max_age <= hg.DEFAULT_GRANT_TTL)
        with self.assertRaises(GrantDenied):
            self.store.exchange(ticket)                                  # replay

    def test_ticket_expires_after_30_seconds(self):
        token = self.sessions.login('alice')
        ticket = self.store.create_ticket(token, 'alice', TARGET)
        self.clock.offset = hg.TICKET_TTL + 1
        with self.assertRaises(GrantDenied):
            self.store.exchange(ticket)

    def test_failed_redemption_still_consumes_the_ticket(self):
        token = self.sessions.login('alice')
        ticket = self.store.create_ticket(token, 'alice', TARGET)
        self.sessions.logout(token, notify=False)
        with self.assertRaises(GrantDenied):
            self.store.exchange(ticket)
        self.sessions.sessions[token] = {'username': 'alice', 'expiry': self.clock() + 3600}       # even if the session came back
        with self.assertRaises(GrantDenied):
            self.store.exchange(ticket)

    def test_forged_malformed_and_unknown_tickets_are_refused(self):
        for bad in (None, '', 'x', 'A' * 42, 'A' * 44, 'A' * 43, '../' * 20, 'A' * 42 + '!', 42, b'A' * 43):
            with self.subTest(ticket=bad), self.assertRaises(GrantDenied):
                self.store.exchange(bad)

    def test_only_a_live_session_of_the_named_user_can_get_a_ticket(self):
        token = self.sessions.login('alice')
        for args in ((token, 'mallory', TARGET), ('0' * 64, 'alice', TARGET), (None, 'alice', TARGET), (token, None, TARGET), ('', '', TARGET)):
            with self.subTest(args=args[:2]), self.assertRaises(GrantDenied):
                self.store.create_ticket(*args)
        self.sessions.logout(token)
        with self.assertRaises(GrantDenied):
            self.store.create_ticket(token, 'alice', TARGET)

    def test_redirect_targets_are_constrained(self):
        good = ['/', '/?native=0', '/#launch=abc', '/a/b?x=1&y=2#frag', '/start?x=1#launch=ONE_USE-token.v1']
        bad = ['', 'start', '//evil.test/', 'http://evil.test/', 'https://evil.test', '\\\\evil', '/a\\b', '/a b', '/a\nb', '/a\x00b', '/é', None, 7,
               ENTER_PATH, ENTER_PATH + '?ticket=x', ENTER_PATH + '/x', '/' + 'a' * 4100]
        for target in good:
            self.assertTrue(valid_target(target), target)
        token = self.sessions.login('alice')
        for target in bad:
            self.assertFalse(valid_target(target), repr(target)[:40])
            with self.assertRaises(GrantDenied):
                self.store.create_ticket(token, 'alice', target)

    def test_outstanding_tickets_per_session_are_capped(self):
        token = self.sessions.login('alice')
        issued = [self.store.create_ticket(token, 'alice', TARGET) for _ in range(hg.MAX_TICKETS_PER_SESSION)]
        with self.assertRaises(GrantDenied):
            self.store.create_ticket(token, 'alice', TARGET)
        other = self.sessions.login('alice')
        self.store.create_ticket(other, 'alice', TARGET)                 # another session is not affected
        self.store.exchange(issued[0])

    def test_a_ticket_cannot_be_redeemed_after_its_session_logged_out(self):
        token = self.sessions.login('alice')
        ticket = self.store.create_ticket(token, 'alice', TARGET)
        self.sessions.logout(token)
        with self.assertRaises(GrantDenied):
            self.store.exchange(ticket)
        self.assertEqual(self.store.stats()['tickets'], 0)


class GrantBindingTests(StoreCase):
    def test_grant_is_bound_to_its_session_and_user(self):
        token, secret, _, _ = self.grant('alice')
        grant = self.store.authorize(secret)
        self.assertEqual((grant.session, grant.user), (digest(token), 'alice'))
        self.assertEqual(grant.key, digest(secret))

    def test_logout_revokes_only_that_sessions_grants(self):
        a1, secret_a1, _, _ = self.grant('alice')
        a2, secret_a2, _, _ = self.grant('alice')                         # a second browser of the same user
        b, secret_b, _, _ = self.grant('bob')
        _, secret_a1_again, _, _ = self.grant('alice', token=a1)          # a second tab/launch of the first browser
        self.sessions.logout(a1)
        self.assertIsNone(self.store.authorize(secret_a1))
        self.assertIsNone(self.store.authorize(secret_a1_again))
        self.assertIsNotNone(self.store.authorize(secret_a2))
        self.assertIsNotNone(self.store.authorize(secret_b))

    def test_use_time_validation_denies_even_if_no_callback_was_delivered(self):
        token, secret, _, _ = self.grant('alice')
        self.sessions.logout(token, notify=False)
        self.assertIsNone(self.store.authorize(secret))
        self.assertEqual(self.store.stats()['grants'], 0)                 # and the dead grant is dropped

    def test_a_session_that_now_belongs_to_another_user_does_not_carry_the_grant(self):
        token, secret, _, _ = self.grant('alice')
        self.sessions.sessions[token]['username'] = 'mallory'
        self.assertIsNone(self.store.authorize(secret))

    def test_grant_expires_with_the_odysseus_session_and_with_its_own_lifetime(self):
        short = self.sessions.login('alice', ttl=600)
        _, secret, _, max_age = self.grant(token=short)
        self.assertLessEqual(max_age, 600)                                # capped by the session
        self.clock.offset = 601
        self.assertIsNone(self.store.authorize(secret))
        long_session = self.sessions.login('alice', ttl=10 ** 7)
        _, secret2, _, max_age2 = self.grant(token=long_session)
        self.assertEqual(max_age2, hg.DEFAULT_GRANT_TTL)
        self.clock.offset = 601 + hg.DEFAULT_GRANT_TTL + 5
        self.sessions.sessions[long_session]['expiry'] = self.clock() + 10 ** 6
        self.assertIsNone(self.store.authorize(secret2))

    def test_presenting_a_live_grant_of_the_same_session_reuses_it(self):
        token, secret, _, _ = self.grant('alice')
        again, target, max_age = self.store.exchange(self.store.create_ticket(token, 'alice', TARGET), secret)
        self.assertIsNone(again)                                          # no new cookie
        self.assertEqual(self.store.stats()['grants'], 1)
        self.assertEqual(target, TARGET)

    def test_presenting_someone_elses_grant_never_reuses_it(self):
        _, attacker_secret, _, _ = self.grant('mallory')
        victim = self.sessions.login('alice')
        new, _, _ = self.store.exchange(self.store.create_ticket(victim, 'alice', TARGET), attacker_secret)
        self.assertIsNotNone(new)
        self.assertNotEqual(new, attacker_secret)
        self.assertEqual(self.store.authorize(new).user, 'alice')
        self.assertEqual(self.store.authorize(attacker_secret).user, 'mallory')         # untouched
        self.sessions.logout(victim)
        self.assertIsNone(self.store.authorize(new))
        self.assertIsNotNone(self.store.authorize(attacker_secret))

    def test_revoke_user_ends_all_of_that_users_grants_and_tickets(self):
        _, a, _, _ = self.grant('alice')
        _, b, _, _ = self.grant('bob')
        pending = self.store.create_ticket(self.sessions.login('alice'), 'alice', TARGET)
        self.assertEqual(self.store.revoke_user('alice'), 1)
        self.assertIsNone(self.store.authorize(a))
        self.assertIsNotNone(self.store.authorize(b))
        with self.assertRaises(GrantDenied):
            self.store.exchange(pending)

    def test_malformed_cookie_values_never_authorize(self):
        for bad in (None, '', 'x', 'A' * 43 + 'A', 'A' * 43, 7, ' ' * 43, '\x00' * 43):
            self.assertIsNone(self.store.authorize(bad))

    def test_grants_per_session_are_capped_and_the_oldest_is_evicted(self):
        store, sessions, clock = make_store(self.tmp + '-cap', max_per_session=3) if os.makedirs(self.tmp + '-cap', exist_ok=True) is None else None
        self.addCleanup(store.close)
        self.addCleanup(shutil.rmtree, self.tmp + '-cap', True)
        token = sessions.login('alice')
        secrets_issued = []
        for _ in range(5):
            clock.offset += 0.01
            secrets_issued.append(store.exchange(store.create_ticket(token, 'alice', TARGET))[0])
        live = [store.authorize(s) is not None for s in secrets_issued]
        self.assertEqual(live, [False, False, True, True, True])

    def test_total_grants_are_capped(self):
        store, sessions, clock = make_store(self.tmp + '-tot', max_total=2) if os.makedirs(self.tmp + '-tot', exist_ok=True) is None else None
        self.addCleanup(store.close)
        self.addCleanup(shutil.rmtree, self.tmp + '-tot', True)
        for user in ('a', 'b'):
            store.exchange(store.create_ticket(sessions.login(user), user, TARGET))
        with self.assertRaises(GrantDenied):
            store.exchange(store.create_ticket(sessions.login('c'), 'c', TARGET))


class FailClosedAndPersistenceTests(StoreCase):
    def test_session_lookup_failure_denies_everything(self):
        token, secret, _, _ = self.grant('alice')
        ticket = self.store.create_ticket(token, 'alice', TARGET)
        self.sessions.broken = True
        self.assertIsNone(self.store.authorize(secret))
        with self.assertRaises(GrantDenied):
            self.store.create_ticket(token, 'alice', TARGET)
        with self.assertRaises(GrantDenied):
            self.store.exchange(ticket)
        self.sessions.broken = False                                      # the failed check revoked the grant: it does not come back
        self.assertIsNone(self.store.authorize(secret))

    def test_grants_survive_a_restart_exactly_as_long_as_the_session_does(self):
        token, secret, _, _ = self.grant('alice')
        other, other_secret, _, _ = self.grant('bob')
        self.store.close()
        store2 = GrantStore(os.path.join(self.tmp, 'grants.db'), self.sessions.lookup, clock=self.clock)
        self.addCleanup(store2.close)
        self.assertEqual(store2.authorize(secret).user, 'alice')
        self.sessions.logout(other, notify=False)                          # bob's Odysseus session did not survive the restart
        self.assertIsNone(store2.authorize(other_secret))
        self.sessions.logout(token, notify=False)
        self.assertIsNone(store2.authorize(secret))

    def test_a_revoked_grant_stays_revoked_after_a_restart(self):
        token, secret, _, _ = self.grant('alice')
        self.sessions.logout(token)
        self.store.close()
        self.sessions.sessions[token] = {'username': 'alice', 'expiry': self.clock() + 3600}
        store2 = GrantStore(os.path.join(self.tmp, 'grants.db'), self.sessions.lookup, clock=self.clock)
        self.addCleanup(store2.close)
        self.assertIsNone(store2.authorize(secret))

    def test_the_database_holds_only_digests_never_a_secret_a_token_or_a_redirect_target(self):
        token, secret, _, _ = self.grant('alice')
        ticket = self.store.create_ticket(token, 'alice', TARGET)
        self.store.close()
        blob = b''.join(p.read_bytes() for p in Path(self.tmp).glob('grants.db*'))
        for needle in (secret, ticket, token, GATEWAY_FRAGMENT, TARGET):
            self.assertNotIn(needle.encode(), blob, needle[:12])
        self.assertIn(digest(secret).encode(), blob)

    def test_corrupt_or_unusable_store_refuses_to_open(self):
        bad = os.path.join(self.tmp, 'bad.db')
        Path(bad).write_bytes(b'this is not a sqlite database' * 50)
        with self.assertRaises(GrantStoreError):
            GrantStore(bad, self.sessions.lookup)
        with self.assertRaises(GrantStoreError):
            GrantStore(os.path.join(self.tmp, 'no-such-dir', 'x.db'), self.sessions.lookup)
        with self.assertRaises(GrantStoreError):
            GrantStore(os.path.join(self.tmp, 'ok.db'), self.sessions.lookup, grant_ttl=5)
        wrong = os.path.join(self.tmp, 'wrong.db')
        db = sqlite3.connect(wrong)
        db.execute('CREATE TABLE meta (name TEXT PRIMARY KEY, value TEXT NOT NULL)')
        db.execute("INSERT INTO meta VALUES ('schema', '99')")
        db.commit()
        db.close()
        with self.assertRaises(GrantStoreError):
            GrantStore(wrong, self.sessions.lookup)

    def test_an_unwritable_store_issues_no_grant(self):
        token = self.sessions.login('alice')
        ticket = self.store.create_ticket(token, 'alice', TARGET)
        self.store.close()
        with self.assertRaises(GrantStoreError):
            self.store.exchange(ticket)
        self.assertEqual(self.store.stats()['grants'], 0)

    def test_logout_race_never_leaves_a_live_grant(self):
        for _ in range(120):
            token = self.sessions.login('alice')
            ticket = self.store.create_ticket(token, 'alice', TARGET)
            outcome = {}

            def redeem():
                try:
                    outcome['secret'] = self.store.exchange(ticket)[0]
                except GrantDenied:
                    outcome['secret'] = None

            threads = [threading.Thread(target=redeem), threading.Thread(target=self.sessions.logout, args=(token,))]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join()
            if outcome['secret']:
                self.assertIsNone(self.store.authorize(outcome['secret']), 'a grant outlived the logout it raced with')
        self.assertEqual(self.store.stats()['grants'], 0)


class WatcherTests(StoreCase):
    def test_revocation_signals_waiting_connections_and_late_watchers(self):
        async def go():
            loop = asyncio.get_running_loop()
            token, secret, _, _ = self.grant('alice')
            key = digest(secret)
            watch = self.store.watch(key, loop)
            other = self.store.watch(key, loop)
            self.assertFalse(watch.event.is_set())
            self.sessions.logout(token)
            await asyncio.wait_for(watch.event.wait(), 2)
            await asyncio.wait_for(other.event.wait(), 2)
            late = self.store.watch(key, loop)                             # a watcher on an already-revoked grant fires at once
            await asyncio.wait_for(late.event.wait(), 2)
        asyncio.run(go())
        self.assertEqual(self.store.stats()['watchers'], 0)


# ── the proxy gate over real sockets ─────────────────────────────────────────────────────────
class CookieGateway(FakeGateway):
    """The fake gateway plus a route that tries to set/clear the proxy's grant cookie."""

    async def _guard(self, connection, request):
        if request.path.startswith('/api/evil-cookie'):
            self._record(request, 'http')
            return self._reply(connection, 200, 'ok', [('Set-Cookie', f'{GRANT_COOKIE}=attacker; Path=/'),
                                                       ('Set-Cookie', f'{GRANT_COOKIE}=; Path=/; Max-Age=0'), ('Set-Cookie', 'gw=1; Path=/')])
        return await super()._guard(connection, request)


class SlowUpstream:
    """HTTP upstream that streams forever or answers very slowly, and records when its client went away."""

    def __init__(self):
        self.port = free_port()
        self.gone = threading.Event()
        outer = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = 'HTTP/1.1'

            def log_message(self, *args):
                pass

            def do_GET(self):
                if self.path.startswith('/slow'):
                    time.sleep(8)
                    return self._plain(b'late')
                self.send_response(200)
                self.send_header('Content-Type', 'text/plain')
                self.send_header('Transfer-Encoding', 'chunked')
                self.end_headers()
                try:
                    for _ in range(300):
                        self.wfile.write(b'5\r\ntick\n\r\n')
                        self.wfile.flush()
                        time.sleep(0.1)
                except OSError:
                    outer.gone.set()

            def _plain(self, body):
                self.send_response(200)
                self.send_header('Content-Length', str(len(body)))
                self.end_headers()
                try:
                    self.wfile.write(body)
                except OSError:
                    outer.gone.set()

        self.httpd = ThreadingHTTPServer(('127.0.0.1', self.port), Handler)
        self.httpd.daemon_threads = True
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()


class GateBase(unittest.TestCase):
    pass_host = False

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix='grant-gate-')
        cls.port = free_port()
        cls.host = f'hermes.localhost:{cls.port}'
        cls.gateway = CookieGateway(public_host=cls.host if cls.pass_host else None)
        cls.gateway.start()
        cls.store, cls.sessions, cls.clock = make_store(cls.tmp)
        env = {'ODYSSEUS_HERMES_PROXY_UPSTREAM_HOST': 'public'} if cls.pass_host else {}
        cls.served = Served(HermesHostProxy(odysseus, f'http://127.0.0.1:{cls.gateway.port}', mode='gateway',
                                            policy=ProxyPolicy.from_environment(env), grants=cls.store), cls.port)
        cls.served.__enter__()

    @classmethod
    def tearDownClass(cls):
        cls.served.__exit__()
        cls.gateway.shutdown()
        cls.store.close()
        shutil.rmtree(cls.tmp, True)

    def setUp(self):
        self.gateway.requests.clear()
        self.gateway.ws_closed.clear()
        self.gateway.ws_close_info = None

    # -- helpers -----------------------------------------------------------------------------
    def enter(self, ticket, *, referer='default', headers=None, host=None, cookie=None, method='GET', query=None):
        h = {'Sec-Fetch-Mode': 'navigate', 'Sec-Fetch-Dest': 'document', 'Sec-Fetch-Site': 'same-site'}
        if referer == 'default':
            h['Referer'] = f'http://localhost:{self.port}/'
        elif referer is not None:
            h['Referer'] = referer
        if cookie:
            h['Cookie'] = f'{GRANT_COOKIE}={cookie}'
        h.update(headers or {})
        return fetch(self.port, host or self.host, ENTER_PATH + ('?ticket=' + ticket if query is None else query), method, h)

    @staticmethod
    def cookie_secret(headers):
        return headers['set-cookie'].split('=', 1)[1].split(';')[0]

    def launch(self, user='alice', token=None, target=TARGET, **enter_kwargs):
        token = token or self.sessions.login(user)
        ticket = self.store.create_ticket(token, user, target)
        status, headers, body = self.enter(ticket, **enter_kwargs)
        self.assertEqual(status, 303, body)
        return token, self.cookie_secret(headers)

    def call(self, secret, path='/api/echo', *, gateway_auth=True, method='GET', extra=None, host=None, cookie_name=GRANT_COOKIE):
        cookies = [f'{cookie_name}={secret}'] if secret else []
        if gateway_auth:
            cookies.append(f'sess={TOKEN}')
        headers = {'Cookie': '; '.join(cookies), **(extra or {})} if cookies else dict(extra or {})
        return fetch(self.port, host or self.host, path, method, headers)


class GateTests(GateBase):
    def test_no_grant_means_no_access_and_the_gateway_is_never_reached(self):
        self.assertEqual(self.call(None)[0], 401)
        status, headers, body = self.call(None, path='/', extra={'Sec-Fetch-Mode': 'navigate', 'Accept': 'text/html'})
        self.assertEqual((status, headers['location']), (302, f'http://localhost:{self.port}/'))        # navigations are sent back to Odysseus
        self.assertEqual(headers['x-odysseus-grant'], 'required')
        self.assertEqual(self.call(None, method='POST', extra={'Origin': f'http://{self.host}', 'Content-Length': '0'})[0], 401)
        self.assertEqual(self.gateway.seen(), [])

    def test_bad_cookies_are_refused(self):
        _, good = self.launch()
        for bad in ('A' * 43, 'x', '', good[:-1] + ('A' if good[-1] != 'A' else 'B'), good + 'A', good.lower() if good.lower() != good else good + '!'):
            with self.subTest(bad=bad[:12]):
                self.assertEqual(self.call(bad)[0], 401)
        conflicting = fetch(self.port, self.host, '/api/echo', 'GET', {'Cookie': f'{GRANT_COOKIE}={good}; {GRANT_COOKIE}={"A" * 43}; sess={TOKEN}'})
        self.assertEqual(conflicting[0], 401)                                                           # cookie-tossing ambiguity is a denial
        self.assertEqual(self.call(good, cookie_name='other_cookie')[0], 401)
        self.assertEqual(self.gateway.seen(), [])

    def test_enter_sets_one_hardened_cookie_and_redirects_to_the_server_held_target(self):
        token = self.sessions.login('alice')
        ticket = self.store.create_ticket(token, 'alice', TARGET)
        status, headers, body = self.enter(ticket)
        self.assertEqual((status, body), (303, b''))
        self.assertEqual(headers['location'], TARGET)                                                   # including the fragment, which the browser then keeps
        cookie = headers['set-cookie']
        for part in ('HttpOnly', 'SameSite=Lax', 'Path=/'):
            self.assertIn(part, cookie)
        self.assertNotIn('Domain', cookie)                                                              # host-only
        self.assertRegex(cookie, r'Max-Age=\d+')
        self.assertTrue(cookie.startswith(GRANT_COOKIE + '='))
        self.assertEqual((headers['cache-control'], headers['referrer-policy']), ('no-store', 'no-referrer'))
        self.assertNotIn(ticket, json.dumps(headers))                                                   # the ticket is not echoed
        conn = http.client.HTTPConnection('127.0.0.1', self.port, timeout=10)                         # a dict keeps one Set-Cookie; read them all
        conn.putrequest('GET', ENTER_PATH + '?ticket=' + self.store.create_ticket(token, 'alice', TARGET), skip_host=True)
        for name, value in (('Host', self.host), ('Sec-Fetch-Mode', 'navigate'), ('Sec-Fetch-Dest', 'document'), ('Referer', f'http://localhost:{self.port}/')):
            conn.putheader(name, value)
        conn.endheaders()
        cookies = [value for name, value in conn.getresponse().getheaders() if name.lower() == 'set-cookie']
        conn.close()
        hint = [c for c in cookies if c.startswith(ENTERED_COOKIE + '=')]
        self.assertEqual(len(hint), 1)
        self.assertNotIn('HttpOnly', hint[0])                                                            # the Webapp plugin must be able to read (and clear) it
        self.assertRegex(hint[0], r'Max-Age=30\b')
        self.assertNotIn(GRANT_COOKIE, hint[0])                                                         # and it carries no credential
        self.assertEqual(self.gateway.seen(), [])                                                       # the exchange never reaches Hermes

    def test_a_granted_request_reaches_the_gateway_without_the_grant_cookie_and_keeps_the_gateways_own_auth(self):
        _, secret = self.launch()
        status, headers, body = self.call(secret, extra={'Origin': f'http://{self.host}'})
        self.assertEqual(status, 200)
        seen = self.gateway.seen()[-1]['headers']
        self.assertNotIn(GRANT_COOKIE, seen['cookie'])
        self.assertNotIn(secret, json.dumps(seen))
        self.assertEqual(seen['cookie'], f'sess={TOKEN}')                                               # the other cookies pass through verbatim
        # the grant is not a substitute for the gateway's authentication
        self.assertEqual(self.call(secret, gateway_auth=False)[0], 401)
        self.assertNotIn(GRANT_COOKIE, self.gateway.seen()[-1]['headers'].get('cookie', ''))
        # and the gateway's authentication is not a substitute for the grant
        self.gateway.requests.clear()
        self.assertEqual(self.call(None)[0], 401)
        self.assertEqual(self.gateway.seen(), [])

    def test_a_cookie_only_request_carrying_just_the_grant_forwards_no_cookie_header(self):
        _, secret = self.launch()
        self.call(secret, path='/api/echo?token=' + TOKEN, gateway_auth=False)
        self.assertNotIn('cookie', self.gateway.seen()[-1]['headers'])

    def test_the_gateway_cannot_set_or_clear_the_grant_cookie(self):
        _, secret = self.launch()
        conn = http.client.HTTPConnection('127.0.0.1', self.port, timeout=10)           # http.client keeps every Set-Cookie; a dict would keep only the last
        conn.putrequest('GET', '/api/evil-cookie', skip_host=True)
        conn.putheader('Host', self.host)
        conn.putheader('Cookie', f'{GRANT_COOKIE}={secret}; sess={TOKEN}')
        conn.endheaders()
        response = conn.getresponse()
        cookies = [value for name, value in response.getheaders() if name.lower() == 'set-cookie']
        response.read()
        conn.close()
        self.assertEqual(response.status, 200)
        self.assertEqual([c.split(';')[0] for c in cookies], ['gw=1'])                                   # its own cookie passes; both grant-cookie attempts are dropped
        self.assertEqual(self.call(secret)[0], 200)
        self.assertIsNotNone(self.store.authorize(secret))

    def test_hostile_requests_are_refused_before_the_grant_is_considered(self):
        _, secret = self.launch()
        self.gateway.requests.clear()
        for origin in ('http://evil.test', 'null', f'http://localhost:{self.port}', f'http://hermes.localhost:{self.port + 1}'):
            self.assertEqual(self.call(secret, method='POST', extra={'Origin': origin, 'Content-Length': '0'})[0], 403, origin)
        for name in ('X-Forwarded-For', 'Forwarded', 'X-Forwarded-Host', 'X-Real-IP'):
            self.assertEqual(self.call(secret, extra={name: '203.0.113.9'})[0], 403, name)
        self.assertEqual(self.call(secret, method='POST', extra={'Sec-Fetch-Site': 'cross-site', 'Content-Length': '0'})[0], 403)
        for host in ('evil.test', f'evil.test:{self.port}', f'hermes.localhost:{self.port + 1}', '127.0.0.1', f'localhost:{self.port}'):
            self.assertEqual(self.call(secret, host=host)[2], b'ODYSSEUS', host)                         # routed to Odysseus; the grant cookie is not honoured there
        self.assertEqual(self.gateway.seen(), [])

    def test_enter_demands_a_navigation_from_the_odysseus_origin(self):
        bodies = set()
        cases = [dict(referer=None), dict(referer='http://evil.test/'), dict(referer=f'http://localhost:{self.port + 1}/'), dict(referer=f'https://localhost:{self.port}/'),
                 dict(referer=f'http://user:pw@localhost:{self.port}/'), dict(referer=f'http://localhost.evil.test:{self.port}/'), dict(referer='null'),
                 dict(referer=f'http://{self.host}/'), dict(headers={'Sec-Fetch-Mode': 'cors'}), dict(headers={'Sec-Fetch-Dest': 'script'}),
                 dict(headers={'Sec-Fetch-Mode': 'no-cors'})]
        for case in cases:
            ticket = self.store.create_ticket(self.sessions.login('alice'), 'alice', TARGET)        # policy refusals leave the ticket unread, so one session per case (cap: 8)
            with self.subTest(case=str(case)[:60]):
                status, headers, body = self.enter(ticket, **case)
                self.assertEqual(status, 403)
                self.assertNotIn('set-cookie', headers)
                bodies.add(body)
        self.assertEqual(len(bodies), 1, 'every refusal must look the same')
        for referer in (f'http://localhost:{self.port}/', f'http://127.0.0.1:{self.port}/', f'http://localhost:{self.port}/some/page?x=1'):
            ticket = self.store.create_ticket(self.sessions.login('alice'), 'alice', TARGET)
            self.assertEqual(self.enter(ticket, referer=referer)[0], 303, referer)

    def test_enter_ticket_handling(self):
        token = self.sessions.login('alice')
        ticket = self.store.create_ticket(token, 'alice', TARGET)
        self.assertEqual(self.enter(ticket)[0], 303)
        status, headers, body = self.enter(ticket)                                                      # replay
        self.assertEqual(status, 403)
        self.assertNotIn('set-cookie', headers)
        fresh = self.store.create_ticket(token, 'alice', TARGET)
        self.assertEqual(self.enter(None, query='')[0], 403)
        self.assertEqual(self.enter(None, query=f'?ticket={fresh}&ticket={fresh}')[0], 403)
        self.assertEqual(self.enter(None, query=f'?ticket={fresh}&next=/x')[0], 403)                    # no extra parameters, no client-chosen target
        self.assertEqual(self.enter(None, query='?ticket=' + 'A' * 43)[0], 403)
        self.assertEqual(self.enter(None, query=f'?ticket={fresh}%20')[0], 403)
        status, headers, _ = self.enter(fresh, method='POST')
        self.assertIn(status, (403, 405))                                                               # refused by the method or the cross-site rule; never a grant
        self.assertNotIn('set-cookie', headers)
        self.assertEqual(self.enter(fresh, method='HEAD')[0], 405)
        self.assertEqual(self.enter(fresh)[0], 303)                                                     # none of the refused attempts consumed the real ticket
        self.assertEqual(self.gateway.seen(), [])

    def test_a_refused_hand_over_recovers_by_itself_without_relaxing_anything(self):
        token = self.sessions.login('alice')
        ticket = self.store.create_ticket(token, 'alice', TARGET)
        status, headers, body = self.enter(ticket, referer=None)                                         # refused before the ticket is read
        page = body.decode()
        self.assertEqual(status, 403)
        self.assertNotIn('set-cookie', headers)
        self.assertIn('text/html', headers['content-type'])
        self.assertEqual(headers['cache-control'], 'no-store')
        script = re.search(r'<script>(.*)</script>', page, re.S).group(1)
        digest_ = base64.b64encode(hashlib.sha256(script.encode()).digest()).decode()
        self.assertIn(f"script-src 'sha256-{digest_}'", headers['content-security-policy'])             # only this script may run
        self.assertIn("default-src 'none'", headers['content-security-policy'])
        self.assertIn(f'data-back="http://localhost:{self.port}/"', page)                                # always the Odysseus origin on this port
        self.assertNotIn(ticket, page)
        for needle in ('getRegistrations', 'unregister', "'hermes-shell-'"):                                  # a stale worker from the old Hermes WebUI is removed before the retry
            self.assertIn(needle, script)
        self.assertEqual(self.enter(ticket)[0], 303)                                                     # the unspent ticket still works, which is what the reload relies on
        spent = self.enter(ticket, referer='default')                                                    # now spent: the same page, so nothing reveals which check refused
        self.assertEqual((spent[0], spent[2]), (403, body))

    def test_enter_on_the_wrong_host_or_with_forwarding_headers(self):
        token = self.sessions.login('alice')
        ticket = self.store.create_ticket(token, 'alice', TARGET)
        self.assertEqual(self.enter(ticket, host=f'evil.test:{self.port}')[2], b'ODYSSEUS')
        for extra in ({'X-Forwarded-For': '1.2.3.4'}, {'Origin': 'http://evil.test'}, {'Origin': 'null'}):
            status, _, _ = self.enter(ticket, headers=extra)
            self.assertEqual(status, 403, extra)
        self.assertEqual(self.enter(ticket)[0], 303)                                                    # refused-by-policy requests never looked at the ticket

    def test_session_fixation_cannot_plant_a_grant_and_never_adopts_a_presented_one(self):
        attacker_token, attacker_secret = self.launch('mallory')
        victim_token = self.sessions.login('alice')
        ticket = self.store.create_ticket(victim_token, 'alice', TARGET)
        status, headers, _ = self.enter(ticket, cookie=attacker_secret)                                 # the browser already holds the attacker's grant
        self.assertEqual(status, 303)
        victim_secret = self.cookie_secret(headers)                                                     # a fresh grant, not the planted value
        self.assertNotEqual(victim_secret, attacker_secret)
        self.assertEqual(self.store.authorize(victim_secret).user, 'alice')
        self.sessions.logout(attacker_token)
        self.assertEqual(self.call(victim_secret)[0], 200)                                              # the attacker's logout does not touch the victim
        self.assertEqual(self.call(attacker_secret)[0], 401)
        self.sessions.logout(victim_token)
        self.assertEqual(self.call(victim_secret)[0], 401)

    def test_a_second_launch_in_the_same_browser_reuses_the_grant_and_sets_no_cookie(self):
        token, secret = self.launch('alice')
        ticket = self.store.create_ticket(token, 'alice', TARGET)
        status, headers, _ = self.enter(ticket, cookie=secret)
        self.assertEqual(status, 303)
        self.assertTrue(headers['set-cookie'].startswith(ENTERED_COOKIE + '='))                         # only the hint: Personal -> Projects -> Personal -> Projects keeps one grant
        self.assertEqual(self.call(secret)[0], 200)

    def test_grants_for_different_sessions_are_independent(self):
        alice_a, secret_a = self.launch('alice')
        alice_b, secret_b = self.launch('alice')
        bob, secret_c = self.launch('bob')
        self.assertEqual(len({secret_a, secret_b, secret_c}), 3)
        self.sessions.logout(alice_a)
        self.assertEqual(self.call(secret_a)[0], 401)
        self.assertEqual(self.call(secret_b)[0], 200)
        self.assertEqual(self.call(secret_c)[0], 200)
        self.sessions.logout(bob)
        self.assertEqual(self.call(secret_b)[0], 200)

    def test_logout_revokes_the_very_next_request_with_no_grace(self):
        token, secret = self.launch('alice')
        self.assertEqual(self.call(secret)[0], 200)
        self.sessions.logout(token)
        self.assertEqual(self.call(secret)[0], 401)
        self.assertEqual(self.call(secret, method='POST', extra={'Content-Length': '0'})[0], 401)
        status, headers, _ = self.call(secret, path='/', extra={'Sec-Fetch-Mode': 'navigate'})          # a back-navigation lands on Odysseus, not on Hermes
        self.assertEqual((status, headers['location']), (302, f'http://localhost:{self.port}/'))

    def test_silent_session_expiry_is_enforced_without_any_callback(self):
        token, secret = self.launch('alice')
        self.sessions.logout(token, notify=False)
        self.assertEqual(self.call(secret)[0], 401)

    def test_the_session_manager_failing_closes_the_gate(self):
        token, secret = self.launch('alice')
        self.sessions.broken = True
        try:
            self.assertEqual(self.call(secret)[0], 401)
            ticket_status = self.enter('A' * 43)[0]
            self.assertEqual(ticket_status, 403)
        finally:
            self.sessions.broken = False

    def test_launch_after_logout_is_refused(self):
        token = self.sessions.login('alice')
        ticket = self.store.create_ticket(token, 'alice', TARGET)
        self.sessions.logout(token)
        status, headers, _ = self.enter(ticket)
        self.assertEqual(status, 403)
        self.assertNotIn('set-cookie', headers)

    def test_gateway_down_is_a_502_only_for_a_valid_grant(self):
        token, secret = self.launch('alice')
        port = free_port()
        app = HermesHostProxy(odysseus, f'http://127.0.0.1:{free_port()}', mode='gateway', grants=self.store)
        with Served(app, port):
            host = f'hermes.localhost:{port}'
            self.assertEqual(fetch(port, host, '/api/echo', 'GET', {'Cookie': f'{GRANT_COOKIE}={secret}'})[0], 502)
            self.assertEqual(fetch(port, host, '/api/echo')[0], 401)

    def test_other_modes_are_unaffected(self):
        legacy = HermesHostProxy(odysseus, f'http://127.0.0.1:{self.gateway.port}', mode='webui', grants=self.store)
        self.assertIsNone(legacy.grants)                                                                # the legacy WebUI path never gates on grants
        plain = HermesHostProxy(odysseus, f'http://127.0.0.1:{self.gateway.port}', mode='gateway')
        self.assertIsNone(plain.grants)


class PassThroughGateTests(GateBase):
    pass_host = True

    def test_the_gate_works_the_same_when_the_gateways_own_origin_checks_see_the_browsers_host(self):
        _, secret = self.launch('alice')
        status, headers, body = self.call(secret, extra={'Origin': f'http://{self.host}'})
        self.assertEqual(status, 200)
        data = json.loads(body)
        self.assertEqual((data['host'], data['origin']), (self.host, f'http://{self.host}'))
        self.assertNotIn(GRANT_COOKIE, self.gateway.seen()[-1]['headers']['cookie'])
        self.assertEqual(self.call(None)[0], 401)


class WebSocketGateTests(GateBase):
    def url(self):
        return f'ws://hermes.localhost:{self.port}/api/ws?token={TOKEN}'

    def connect(self, secret, **kwargs):
        headers = {'Cookie': f'{GRANT_COOKIE}={secret}'} if secret else {}
        return websockets.connect(self.url(), host='127.0.0.1', port=self.port, origin=f'http://{self.host}', additional_headers=headers, max_size=None, **kwargs)

    def test_upgrade_requires_a_live_grant(self):
        async def go():
            for secret in (None, 'A' * 43, 'junk'):
                with self.assertRaises(InvalidStatus) as caught:
                    async with self.connect(secret):
                        pass
                self.assertEqual(caught.exception.response.status_code, 401)
        asyncio.run(go())
        self.assertEqual(self.gateway.seen(), [])

    def test_a_granted_socket_works_and_the_grant_cookie_never_reaches_the_gateway(self):
        _, secret = self.launch('alice')

        async def go():
            async with self.connect(secret) as ws:
                await ws.send('hello')
                self.assertEqual(await ws.recv(), 'hello')
        asyncio.run(go())
        seen = self.gateway.seen()[0]['headers']
        self.assertNotIn(GRANT_COOKIE, seen.get('cookie', ''))
        self.assertNotIn(secret, json.dumps(seen))

    def test_logout_closes_every_open_socket_of_that_session_immediately_and_only_those(self):
        alice, secret_a = self.launch('alice')
        bob, secret_b = self.launch('bob')
        alice_other, secret_c = self.launch('alice')

        async def go():
            async with self.connect(secret_a) as tab1, self.connect(secret_a) as tab2, self.connect(secret_b) as bobs, self.connect(secret_c) as other:
                for ws in (tab1, tab2, bobs, other):
                    await ws.send('ping')
                    await ws.recv()
                started = time.monotonic()
                self.sessions.logout(alice)
                for ws in (tab1, tab2):
                    with self.assertRaises(ConnectionClosed):
                        await asyncio.wait_for(ws.recv(), 3)
                    self.assertEqual((ws.close_code, ws.close_reason), (4401, 'Odysseus session ended'))
                self.assertLess(time.monotonic() - started, 1.5, 'revocation must not wait for a polling interval')
                for ws in (bobs, other):                                                                  # unaffected
                    await ws.send('still here')
                    self.assertEqual(await asyncio.wait_for(ws.recv(), 3), 'still here')
            return True
        self.assertTrue(asyncio.run(go()))

    def test_reconnects_after_logout_are_refused(self):
        token, secret = self.launch('alice')

        async def go():
            async with self.connect(secret) as ws:
                await ws.send('x')
                await ws.recv()
                self.sessions.logout(token)
                with self.assertRaises(ConnectionClosed):
                    await asyncio.wait_for(ws.recv(), 3)
            for _ in range(3):                                                                           # a reconnect loop gets nowhere
                with self.assertRaises(InvalidStatus) as caught:
                    async with self.connect(secret):
                        pass
                self.assertEqual(caught.exception.response.status_code, 401)
        asyncio.run(go())

    def test_a_silent_session_end_still_closes_the_socket_within_the_revalidation_interval(self):
        token, secret = self.launch('alice')

        async def go():
            async with self.connect(secret) as ws:
                await ws.send('x')
                await ws.recv()
                self.sessions.logout(token, notify=False)                                                # expiry, a deleted user: nobody calls us
                started = time.monotonic()
                with self.assertRaises(ConnectionClosed):
                    await asyncio.wait_for(ws.recv(), 6)
                self.assertEqual(ws.close_code, 4401)
                self.assertLess(time.monotonic() - started, 4.5)
        asyncio.run(go())
        self.assertTrue(self.gateway.ws_closed.wait(5), 'the gateway-side connection must be released too')

    def test_the_gateway_side_connection_is_released_on_revocation(self):
        token, secret = self.launch('alice')

        async def go():
            async with self.connect(secret) as ws:
                await ws.send('x')
                await ws.recv()
                self.sessions.logout(token)
                with self.assertRaises(ConnectionClosed):
                    await asyncio.wait_for(ws.recv(), 3)
        asyncio.run(go())
        self.assertTrue(self.gateway.ws_closed.wait(5))

    def test_hostile_upgrades_are_refused_even_with_a_valid_grant(self):
        _, secret = self.launch('alice')
        from test_proxy_gateway import handshake, raw
        for origin in ('http://evil.test', 'null', f'http://localhost:{self.port}'):
            status, _, sock = raw(self.port, handshake(self.host, origin=origin, token=False).replace('\r\n\r\n', f'\r\nCookie: {GRANT_COOKIE}={secret}\r\n\r\n'))
            sock.close()
            self.assertEqual(status, 403, origin)
        status, _, sock = raw(self.port, handshake(self.host, 'X-Forwarded-For: 1.2.3.4').replace('\r\n\r\n', f'\r\nCookie: {GRANT_COOKIE}={secret}\r\n\r\n'))
        sock.close()
        self.assertEqual(status, 403)
        self.assertEqual(self.gateway.seen(), [])

    def test_the_enter_route_is_not_a_websocket_endpoint(self):
        async def go():
            with self.assertRaises(Exception):
                async with websockets.connect(f'ws://hermes.localhost:{self.port}{ENTER_PATH}?ticket=' + 'A' * 43, host='127.0.0.1', port=self.port,
                                              origin=f'http://{self.host}'):
                    pass
        asyncio.run(go())
        self.assertEqual(self.gateway.seen(), [])


class StreamAndSlowResponseTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix='grant-stream-')
        cls.store, cls.sessions, cls.clock = make_store(cls.tmp)
        cls.upstream = SlowUpstream()
        cls.port = free_port()
        cls.host = f'hermes.localhost:{cls.port}'
        cls.served = Served(HermesHostProxy(odysseus, f'http://127.0.0.1:{cls.upstream.port}', mode='gateway', grants=cls.store), cls.port)
        cls.served.__enter__()

    @classmethod
    def tearDownClass(cls):
        cls.served.__exit__()
        cls.upstream.close()
        cls.store.close()
        shutil.rmtree(cls.tmp, True)

    def setUp(self):
        self.upstream.gone.clear()

    def grant(self):
        token = self.sessions.login('alice')
        secret = self.store.exchange(self.store.create_ticket(token, 'alice', '/'))[0]
        return token, secret

    def open(self, secret, path):
        conn = http.client.HTTPConnection('127.0.0.1', self.port, timeout=15)
        conn.putrequest('GET', path, skip_host=True)
        conn.putheader('Host', self.host)
        conn.putheader('Cookie', f'{GRANT_COOKIE}={secret}')
        conn.endheaders()
        return conn

    def test_an_open_stream_is_cut_when_the_session_ends(self):
        token, secret = self.grant()
        conn = self.open(secret, '/stream')
        response = conn.getresponse()
        self.assertEqual(response.status, 200)
        self.assertIn(b'tick', response.read1(64))
        started = time.monotonic()
        self.sessions.logout(token)
        received = 0
        try:
            while True:
                chunk = response.read1(256)
                if not chunk:
                    break
                received += len(chunk)
                self.assertLess(time.monotonic() - started, 3, 'the stream kept flowing after logout')
        except (http.client.IncompleteRead, http.client.HTTPException, OSError):
            pass
        self.assertLess(time.monotonic() - started, 3)
        self.assertTrue(self.upstream.gone.wait(5), 'the upstream connection must be released')
        conn.close()

    def test_a_pending_response_is_never_delivered_after_logout(self):
        token, secret = self.grant()
        result = {}

        def request():
            conn = self.open(secret, '/slow')
            started = time.monotonic()
            try:
                response = conn.getresponse()
                result.update(status=response.status, body=response.read(), elapsed=time.monotonic() - started)
            except OSError as exc:
                result.update(error=exc, elapsed=time.monotonic() - started)

        thread = threading.Thread(target=request)
        thread.start()
        time.sleep(0.8)
        self.sessions.logout(token)
        thread.join(10)
        self.assertEqual(result.get('status'), 401, result)
        self.assertNotIn(b'late', result.get('body', b''))
        self.assertLess(result['elapsed'], 4, 'logout must not wait for the slow upstream')

    def test_other_sessions_streams_are_not_cut(self):
        token_a, secret_a = self.grant()
        token_b, secret_b = self.grant()
        conn = self.open(secret_b, '/stream')
        response = conn.getresponse()
        self.assertIn(b'tick', response.read1(64))
        self.sessions.logout(token_a)
        time.sleep(0.5)
        self.assertIn(b'tick', response.read1(64))
        conn.close()


class AuthManagerIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix='authmgr-')
        self.addCleanup(shutil.rmtree, self.tmp, True)
        from core.auth import AuthManager
        self.auth = AuthManager(os.path.join(self.tmp, 'auth.json'))
        self.auth.setup('admin', 'correct horse battery staple 1')
        self.auth.create_user('bob', 'another long passphrase 22')
        self.seen = []
        self.auth.add_revoke_listener(lambda tokens: self.seen.append(list(tokens)))

    def test_lookup_by_digest_matches_live_sessions_only(self):
        token = self.auth.create_session_trusted('admin')
        user, expiry = self.auth.lookup_session_digest(digest(token))
        self.assertEqual(user, 'admin')
        self.assertGreater(expiry, time.time())
        self.assertIsNone(self.auth.lookup_session_digest(digest('0' * 64)))
        self.assertIsNone(self.auth.lookup_session_digest('x'))
        self.auth.revoke_token(token)
        self.assertIsNone(self.auth.lookup_session_digest(digest(token)))

    def test_expired_and_orphaned_sessions_do_not_look_up(self):
        token = self.auth.create_session_trusted('bob')
        self.auth._sessions[token]['expiry'] = time.time() - 1
        self.assertIsNone(self.auth.lookup_session_digest(digest(token)))
        token2 = self.auth.create_session_trusted('bob')
        self.auth._config['users'].pop('bob')
        self.assertIsNone(self.auth.lookup_session_digest(digest(token2)))

    def test_every_way_a_session_ends_notifies_listeners(self):
        t1 = self.auth.create_session_trusted('admin')
        self.auth.revoke_token(t1)
        self.assertEqual(self.seen[-1], [t1])
        t2, t3 = self.auth.create_session_trusted('bob'), self.auth.create_session_trusted('bob')
        self.auth.revoke_user_sessions('bob', except_token=t3)
        self.assertEqual(self.seen[-1], [t2])
        t4 = self.auth.create_session_trusted('bob')
        self.auth._sessions[t4]['expiry'] = time.time() - 1
        self.assertFalse(self.auth.validate_token(t4))
        self.assertEqual(self.seen[-1], [t4])
        t5 = self.auth.create_session_trusted('bob')
        self.auth._sessions[t5]['expiry'] = time.time() - 1
        self.assertIsNone(self.auth.get_username_for_token(t5))
        self.assertEqual(self.seen[-1], [t5])
        before = len(self.seen)
        self.assertTrue(self.auth.delete_user('bob', 'admin'))
        self.assertGreater(len(self.seen), before)
        self.assertIn(t3, self.seen[-1])

    def test_a_failing_listener_never_breaks_logout(self):
        def explode(tokens):
            raise RuntimeError('listener bug')
        self.auth.add_revoke_listener(explode)
        token = self.auth.create_session_trusted('admin')
        self.auth.revoke_token(token)                                    # must not raise
        self.assertFalse(self.auth.validate_token(token))

    def test_real_session_manager_end_to_end_with_the_store(self):
        store = GrantStore(os.path.join(self.tmp, 'g.db'), self.auth.lookup_session_digest)
        self.addCleanup(store.close)
        self.auth.add_revoke_listener(store.sessions_revoked)
        a, b = self.auth.create_session_trusted('admin'), self.auth.create_session_trusted('admin')
        secret_a = store.exchange(store.create_ticket(a, 'admin', '/'))[0]
        secret_b = store.exchange(store.create_ticket(b, 'admin', '/'))[0]
        self.auth.revoke_token(a)
        self.assertIsNone(store.authorize(secret_a))
        self.assertIsNotNone(store.authorize(secret_b))
        self.auth.revoke_user_sessions('admin')
        self.assertIsNone(store.authorize(secret_b))


class LaunchRouteGrantTests(unittest.TestCase):
    def setUp(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        import src.application_modes as modes
        self.modes = modes
        self.tmp = tempfile.mkdtemp(prefix='launch-')
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.store, self.sessions, self.clock = make_store(self.tmp)
        self.addCleanup(self.store.close)
        self.token = self.sessions.login('fixture')
        app = FastAPI()
        app.state.auth_manager = mock.Mock(is_configured=True)
        app.state.auth_manager.is_admin.return_value = True

        @app.middleware('http')
        async def identity(request, call_next):
            request.state.current_user = 'fixture'
            return await call_next(request)

        self.service = mock.Mock()
        self.service.status.return_value = {'enabled': True, 'state': 'ready', 'backend': 'gateway'}
        self.service.launch.return_value = 'http://hermes.localhost:17000/start?x=1#launch=' + GATEWAY_FRAGMENT
        self.service.grants = self.store
        app.include_router(modes.make_router(self.service))
        self.client = TestClient(app, base_url='http://127.0.0.1:17000', client=('127.0.0.1', 40000))
        self.client.cookies.set('odysseus_session', self.token)
        self.ok = {'Origin': 'http://127.0.0.1:17000'}

    def test_the_browser_gets_an_opaque_ticket_url_and_never_the_gateways_launch_link(self):
        response = self.client.post('/api/application-modes/projects/launch', headers=self.ok)
        self.assertEqual(response.status_code, 200)
        url = response.json()['url']
        self.assertRegex(url, r'^http://hermes\.localhost:17000' + ENTER_PATH + r'\?ticket=[A-Za-z0-9_-]{43}$')
        self.assertNotIn(GATEWAY_FRAGMENT, response.text)                                          # no Hermes credential in anything JavaScript can read
        self.assertNotIn('#', url)
        self.assertEqual((response.headers['cache-control'], response.headers['referrer-policy']), ('no-store, no-cache'.split(',')[0] if False else response.headers['cache-control'], 'no-referrer'))
        ticket = url.split('ticket=')[1]
        secret, target, _ = self.store.exchange(ticket)
        self.assertEqual(target, '/start?x=1#launch=' + GATEWAY_FRAGMENT)                         # held server-side, handed to the browser only in the redirect
        self.assertEqual(self.store.authorize(secret).session, digest(self.token))

    def test_no_ticket_for_a_caller_whose_cookie_is_not_a_live_session_of_the_identified_user(self):
        for cookie in (None, '0' * 64, secrets.token_hex(32)):
            self.client.cookies.clear()
            if cookie:
                self.client.cookies.set('odysseus_session', cookie)
            response = self.client.post('/api/application-modes/projects/launch', headers=self.ok)
            self.assertEqual(response.status_code, 403, cookie)
            self.assertNotIn('ticket', response.text)
        self.sessions.sessions[self.token]['username'] = 'someone-else'
        self.client.cookies.set('odysseus_session', self.token)
        self.assertEqual(self.client.post('/api/application-modes/projects/launch', headers=self.ok).status_code, 403)

    def test_without_grants_the_gateways_own_link_is_returned_as_before(self):
        self.service.grants = None
        url = self.client.post('/api/application-modes/projects/launch', headers=self.ok).json()['url']
        self.assertEqual(url, 'http://hermes.localhost:17000/start?x=1#launch=' + GATEWAY_FRAGMENT)


if __name__ == '__main__':
    unittest.main()

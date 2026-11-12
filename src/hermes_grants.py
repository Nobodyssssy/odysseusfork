"""Server-side access grants for the Hermes Projects origin (hermes.localhost), bound to an Odysseus browser session.

Why this exists: the Odysseus session cookie is host-only on localhost, so the Hermes origin never sees it, and the official gateway's own
sessions are stateless (its logout clears one browser's cookies and revokes nothing server-side). Without a gate, logging out of Odysseus would
leave every open Hermes tab working. A grant is an Odysseus-side record, created only for an authenticated Odysseus administrator and valid
exactly as long as that Odysseus session is. The proxy admits Hermes traffic only while the grant is live. It is an ADDITIONAL gate: the gateway's
own authentication is untouched and still decides every request that gets through.

Records (only SHA-256 digests are kept; the secrets live in one browser cookie and in memory for the length of one request):
  ticket  one-use, 30 s, memory only. Bound to the Odysseus session, its user and a server-held redirect target (which may contain the gateway's own
          one-use launch link, so no Hermes credential ever passes through Odysseus JavaScript).
  grant   created by redeeming a ticket. Bound to digest(Odysseus session token) and the username; expires at min(TTL, Odysseus session expiry);
          persisted so it survives an Odysseus restart exactly as long as the Odysseus session does.
Every use re-checks that the Odysseus session is still live and still belongs to the same user, so a revoked or expired session invalidates its
grants on the very next request even if a revocation callback was never delivered. Revocation additionally closes proxy-managed sockets and streams.

Not a protection against: a copied upstream Hermes credential, or anything that reaches the gateway's own port directly. See docs/hermes-webapp-grants.md.
"""
import asyncio
import hashlib
import logging
import re
import secrets
import sqlite3
import threading
import time
from dataclasses import dataclass

GRANT_COOKIE = 'odysseus_hermes_grant'
ENTER_PATH = '/__odysseus/enter'
ENTERED_COOKIE = 'odysseus_entered'       # a non-secret 30-second hint that this tab arrived from Odysseus (lets the Webapp's "Personal" link use history)
TICKET_TTL = 30
DEFAULT_GRANT_TTL = 8 * 3600
MAX_GRANTS_PER_SESSION = 16
MAX_TICKETS_PER_SESSION = 8
MAX_GRANTS_TOTAL = 256
SECRET_PATTERN = re.compile(r'^[A-Za-z0-9_-]{43}$')
_TARGET = re.compile(r'^/[\x21-\x7e]*$')
log = logging.getLogger('odysseus.hermes_grants')


class GrantError(Exception):
    """Base class; messages are safe to log and never contain a secret."""


class GrantDenied(GrantError):
    """The request is refused (generic on purpose: no oracle for which condition failed). ``reason`` is a short code for the server log only; it is never sent
    to the browser."""

    def __init__(self, message='refused', reason='refused'):
        super().__init__(message)
        self.reason = reason


class GrantStoreError(GrantError):
    """The store cannot be used; callers must fail closed."""


def digest(value):
    return hashlib.sha256(value.encode('utf-8')).hexdigest()


def valid_target(target):
    """A server-side redirect target: absolute path (+ optional query and fragment), printable ASCII, never protocol-relative, never the enter route."""
    return (isinstance(target, str) and len(target) <= 4096 and bool(_TARGET.match(target)) and not target.startswith('//')
            and '\\' not in target and not target.split('?', 1)[0].split('#', 1)[0].startswith(ENTER_PATH))


@dataclass(frozen=True)
class Grant:
    key: str            # sha256 of the grant secret
    session: str        # sha256 of the Odysseus session token
    user: str
    expires: float

    @property
    def short(self):
        return self.key[:8]


class Watch:
    """A live connection (WebSocket relay or streamed response) that must end when its grant does."""

    def __init__(self, loop):
        self.loop = loop
        self.event = asyncio.Event()

    def trigger(self):
        try:
            self.loop.call_soon_threadsafe(self.event.set)
        except RuntimeError:                    # the loop is already closed; the connection is gone anyway
            pass


class GrantStore:
    def __init__(self, path, session_lookup, *, grant_ttl=DEFAULT_GRANT_TTL, max_per_session=MAX_GRANTS_PER_SESSION,
                 max_total=MAX_GRANTS_TOTAL, clock=time.time):
        """``session_lookup(session_digest)`` returns ``(username, expiry_epoch)`` for a live Odysseus session, else None. It may raise;
        that is treated as 'cannot validate' and denies."""
        self._lookup = session_lookup
        self._ttl = int(grant_ttl)
        self._max_per_session = int(max_per_session)
        self._max_total = int(max_total)
        self._clock = clock
        self._lock = threading.RLock()
        self._tickets = {}
        self._grants = {}
        self._watchers = {}
        self._db = None
        if self._ttl < 60:
            raise GrantStoreError('The grant lifetime must be at least 60 seconds.')
        self._open(path)

    # -- persistence ------------------------------------------------------------------------
    def _open(self, path):
        try:
            self._db = sqlite3.connect(str(path), check_same_thread=False, timeout=5, isolation_level=None)
            self._db.execute('CREATE TABLE IF NOT EXISTS grants (key TEXT PRIMARY KEY, session TEXT NOT NULL, user TEXT NOT NULL, '
                             'created REAL NOT NULL, expires REAL NOT NULL)')
            self._db.execute('CREATE TABLE IF NOT EXISTS meta (name TEXT PRIMARY KEY, value TEXT NOT NULL)')
            self._db.execute("INSERT OR IGNORE INTO meta VALUES ('schema', '1')")
            if self._db.execute("SELECT value FROM meta WHERE name='schema'").fetchone()[0] != '1':
                raise GrantStoreError('Unsupported grant store version.')
            now = self._clock()
            self._db.execute('DELETE FROM grants WHERE expires <= ?', (now,))
            for key, session, user, created, expires in self._db.execute('SELECT key, session, user, created, expires FROM grants'):
                self._grants[key] = {'session': session, 'user': user, 'created': created, 'expires': expires}
        except sqlite3.Error as exc:
            raise GrantStoreError('The grant store could not be opened; Projects access stays closed.') from exc

    def _persist(self, sql, *args):
        if self._db is None:
            raise GrantStoreError('The grant store is closed.')
        try:
            self._db.execute(sql, args)
        except sqlite3.Error as exc:
            raise GrantStoreError('The grant store could not be written.') from exc

    def close(self):
        with self._lock:
            if self._db is not None:
                self._db.close()
                self._db = None

    # -- Odysseus session check -------------------------------------------------------------
    def _session(self, session_digest):
        try:
            return self._lookup(session_digest)
        except Exception:                                            # cannot validate -> deny
            log.error('Odysseus session lookup failed; denying', exc_info=True)
            return None

    def _live(self, session_digest, user):
        info = self._session(session_digest)
        return info if info and info[0] == user and info[1] > self._clock() else None

    # -- tickets ----------------------------------------------------------------------------
    def create_ticket(self, token, user, target):
        """Called only from the authenticated, same-origin, administrator-only launch route."""
        if not (isinstance(token, str) and token and isinstance(user, str) and user) or not valid_target(target):
            raise GrantDenied('refused')
        session = digest(token)
        if not self._live(session, user):
            raise GrantDenied('refused')
        with self._lock:
            now = self._clock()
            self._tickets = {key: rec for key, rec in self._tickets.items() if rec['exp'] > now}
            if sum(1 for rec in self._tickets.values() if rec['session'] == session) >= MAX_TICKETS_PER_SESSION:
                raise GrantDenied('refused')
            ticket = secrets.token_urlsafe(32)
            self._tickets[digest(ticket)] = {'session': session, 'user': user, 'target': target, 'exp': now + TICKET_TTL}
            return ticket

    def exchange(self, ticket, presented=None):
        """Redeem a ticket. Returns ``(grant_secret_or_None, target, max_age)``: None means the browser already holds a live grant for the same
        Odysseus session and keeps it (no new cookie, no accumulation). The ticket is consumed whatever the outcome."""
        if not (isinstance(ticket, str) and SECRET_PATTERN.match(ticket)):
            raise GrantDenied('refused', 'ticket-malformed')
        with self._lock:
            record = self._tickets.pop(digest(ticket), None)
            now = self._clock()
            if record is None:
                raise GrantDenied('refused', 'ticket-unknown-or-already-used')
            if record['exp'] <= now:
                raise GrantDenied('refused', 'ticket-expired')
            info = self._live(record['session'], record['user'])
            if not info:
                raise GrantDenied('refused', 'odysseus-session-not-live')
            if isinstance(presented, str) and SECRET_PATTERN.match(presented):
                existing = self._grants.get(digest(presented))
                if existing and existing['session'] == record['session'] and existing['user'] == record['user'] and existing['expires'] > now:
                    return None, record['target'], int(existing['expires'] - now)
            self._grants = {key: g for key, g in self._grants.items() if g['expires'] > now}
            if len(self._grants) >= self._max_total:
                raise GrantDenied('refused', 'grant-capacity')
            mine = sorted((g['created'], key) for key, g in self._grants.items() if g['session'] == record['session'])
            while len(mine) >= self._max_per_session:
                self._revoke_key(mine.pop(0)[1], 'evicted')
            secret = secrets.token_urlsafe(32)
            key = digest(secret)
            expires = min(now + self._ttl, info[1])
            self._persist('INSERT INTO grants VALUES (?, ?, ?, ?, ?)', key, record['session'], record['user'], now, expires)
            self._grants[key] = {'session': record['session'], 'user': record['user'], 'created': now, 'expires': expires}
            log.info('grant %s issued (%d for this session)', key[:8], len(mine) + 1)
            return secret, record['target'], int(expires - now)

    # -- use ----------------------------------------------------------------------------------
    def authorize(self, secret):
        """The live Grant for a presented cookie value, else None. Re-validates the Odysseus session every time."""
        if not (isinstance(secret, str) and SECRET_PATTERN.match(secret)):
            return None
        return self.check(digest(secret))

    def check(self, key):
        with self._lock:
            grant = self._grants.get(key)
            if grant is None:
                return None
            if grant['expires'] <= self._clock():
                self._revoke_key(key, 'expired')
                return None
            if not self._live(grant['session'], grant['user']):
                self._revoke_key(key, 'session ended')
                return None
            return Grant(key, grant['session'], grant['user'], grant['expires'])

    def watch(self, key, loop):
        watch = Watch(loop)
        with self._lock:
            if key not in self._grants:           # already revoked: fire at once and register nothing that could leak
                watch.trigger()
                return watch
            self._watchers.setdefault(key, set()).add(watch)
        return watch

    def unwatch(self, key, watch):
        with self._lock:
            watchers = self._watchers.get(key)
            if watchers:
                watchers.discard(watch)
                if not watchers:
                    del self._watchers[key]

    # -- revocation ----------------------------------------------------------------------------
    def _revoke_key(self, key, reason):
        self._grants.pop(key, None)
        try:
            self._persist('DELETE FROM grants WHERE key = ?', key)
        except GrantStoreError:
            log.error('could not delete grant %s from disk; it is revoked in memory and will fail validation after a restart', key[:8])
        for watch in list(self._watchers.pop(key, ())):
            watch.trigger()
        log.info('grant %s revoked (%s)', key[:8], reason)

    def revoke_session(self, session_digest):
        with self._lock:
            keys = [key for key, g in self._grants.items() if g['session'] == session_digest]
            for ticket_key in [k for k, rec in self._tickets.items() if rec['session'] == session_digest]:
                del self._tickets[ticket_key]
            for key in keys:
                self._revoke_key(key, 'odysseus logout')
            return len(keys)

    def revoke_user(self, user):
        with self._lock:
            keys = [key for key, g in self._grants.items() if g['user'] == user]
            for ticket_key in [k for k, rec in self._tickets.items() if rec['user'] == user]:
                del self._tickets[ticket_key]
            for key in keys:
                self._revoke_key(key, 'user revoked')
            return len(keys)

    def sessions_revoked(self, tokens):
        """AuthManager callback: Odysseus session tokens that were just revoked."""
        for token in tokens:
            try:
                self.revoke_session(digest(token))
            except Exception:                                        # never break the logout that called us; use-time validation still denies
                log.error('grant revocation callback failed', exc_info=True)

    def stats(self):
        with self._lock:
            return {'grants': len(self._grants), 'tickets': len(self._tickets), 'watchers': sum(len(w) for w in self._watchers.values())}

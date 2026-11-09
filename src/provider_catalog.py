"""Read-only provider catalog shared by Odysseus and Hermes.

Pure logic: normalization, identity, merge and status. Adapters supply plain data;
nothing here reads or returns an API key, token or cookie. Credentials are reduced
to booleans (has_credential) at the adapter boundary and never enter this module.
"""
from dataclasses import dataclass, field
import hashlib
import ipaddress
import json
from urllib.parse import urlsplit

LOOPBACK_NAMES = {'localhost', '127.0.0.1', '::1', '0.0.0.0', '[::1]'}
_SUFFIXES = ('/chat/completions', '/completions', '/models')

AUTH_NONE, AUTH_KEY, AUTH_OAUTH, AUTH_PROCESS = 'none', 'api_key', 'oauth', 'process'

# Connection / sharing states shown to the user.
CONNECTED, REQUIRES_AUTH, UNSUPPORTED, OFFLINE, DISABLED, UNKNOWN = (
    'connected', 'requires_authentication', 'unsupported', 'offline', 'disabled', 'unknown')


def canonical_url(url):
    """Stable endpoint identity, or None when unusable or when it embeds credentials.

    localhost, 127.0.0.1, ::1 and 0.0.0.0 are one machine and map to 127.0.0.1.
    Scheme and host are lower-cased, default ports dropped, query/fragment removed,
    and completion/model suffixes stripped so equal servers compare equal.
    """
    if not isinstance(url, str) or not url.strip() or len(url) > 2048:
        return None
    try:
        parts = urlsplit(url.strip())
        host = (parts.hostname or '').lower()
        port = parts.port
    except ValueError:
        return None
    if parts.scheme not in ('http', 'https') or not host or parts.username or parts.password:
        return None
    if host in LOOPBACK_NAMES:
        host = '127.0.0.1'
    else:
        try:
            ip = ipaddress.ip_address(host)
            host = ip.compressed
            if ip.version == 6:
                host = '[' + host + ']'
        except ValueError:
            pass
    if port and (parts.scheme, port) in (('http', 80), ('https', 443)):
        port = None
    path = parts.path.rstrip('/')
    for suffix in _SUFFIXES:
        if path.endswith(suffix):
            path = path[:-len(suffix)].rstrip('/')
    return f'{parts.scheme}://{host}{":" + str(port) if port else ""}{path}'


def is_loopback(canonical):
    return bool(canonical) and urlsplit(canonical).hostname == '127.0.0.1'


def redact_url(url):
    """Display-safe URL: credentials, query and fragment removed."""
    try:
        parts = urlsplit(str(url))
        host = parts.hostname or ''
        netloc = host + (':' + str(parts.port) if parts.port else '')
        return f'{parts.scheme}://{netloc}{parts.path}' if parts.scheme and host else '[unusable URL]'
    except ValueError:
        return '[unusable URL]'


@dataclass(frozen=True)
class Entry:
    """One provider as one application knows it."""
    source: str            # 'odysseus' | 'hermes'
    native_id: str         # the application's own id
    name: str
    base_url: str | None   # redacted display URL
    canonical: str | None
    models: tuple = ()
    auth: str = AUTH_NONE
    has_credential: bool = False
    enabled: bool = True
    reachable: bool | None = None     # app-reported or probed; None = not checked
    reason: str = ''                  # non-secret explanation, e.g. an auth error class
    self_hosted: bool = False

    @property
    def identity(self):
        """Cross-application key. Endpoint-defined providers match by canonical URL;
        native providers (no URL) match by their own id and never match a different id."""
        return 'ep:' + self.canonical if self.canonical else f'native:{self.source}:{self.native_id}'

    @property
    def portable(self):
        """Whether the provider *definition* can be mirrored. Credentials are separate."""
        return bool(self.canonical) and self.auth in (AUTH_NONE, AUTH_KEY)

    def definition_fingerprint(self):
        payload = json.dumps({'url': self.canonical, 'auth': self.auth, 'models': sorted(self.models)}, sort_keys=True)
        return hashlib.sha256(payload.encode()).hexdigest()[:16]


def connection_state(entry):
    if not entry.enabled:
        return DISABLED
    if entry.auth == AUTH_PROCESS:
        return UNSUPPORTED
    if entry.auth in (AUTH_KEY, AUTH_OAUTH) and not entry.has_credential:
        return REQUIRES_AUTH
    if entry.reachable is False:
        return OFFLINE
    if entry.reachable is True or (entry.has_credential and entry.reachable is None):
        return CONNECTED
    return UNKNOWN


@dataclass
class SyncState:
    """Metadata owned by the sync layer only. Never holds secrets."""
    version: int = 1
    share: dict = field(default_factory=dict)        # identity -> bool (explicit opt-in)
    tombstones: dict = field(default_factory=dict)   # identity -> {'by': app, 'fingerprint': str}
    applied: dict = field(default_factory=dict)      # identity -> {'target': app, 'fingerprint': str}

    @classmethod
    def load(cls, data):
        if not isinstance(data, dict) or data.get('version') != 1:
            return cls()
        def pick(name):
            value = data.get(name)
            return value if isinstance(value, dict) else {}
        return cls(1, {k: v for k, v in pick('share').items() if isinstance(v, bool)}, dict(pick('tombstones')), dict(pick('applied')))

    def dump(self):
        return {'version': 1, 'share': self.share, 'tombstones': self.tombstones, 'applied': self.applied}


def build_catalog(odysseus, hermes, state=None):
    """Merge both applications' entries into one view. Pure and idempotent."""
    state = state or SyncState()
    groups = {}
    for entry in list(odysseus) + list(hermes):
        groups.setdefault(entry.identity, {})
        if entry.source in groups[entry.identity]:
            # Two providers on one endpoint inside one app are a duplicate, not two rows.
            groups[entry.identity][entry.source + '_duplicates'] = groups[entry.identity].get(entry.source + '_duplicates', 0) + 1
            continue
        groups[entry.identity][entry.source] = entry
    rows = []
    for identity, found in sorted(groups.items()):
        od, he = found.get('odysseus'), found.get('hermes')
        first = od or he
        models = sorted({m for e in (od, he) if e for m in e.models})
        portable = all(e.portable for e in (od, he) if e)
        shared_opt_in = state.share.get(identity, False)
        rows.append({
            'id': identity,
            'name': first.name,
            'base_url': first.base_url,
            'source': 'both' if od and he else first.source,
            'models': models,
            'models_by_app': {a: sorted(e.models) for a, e in (('odysseus', od), ('hermes', he)) if e},
            'status': {a: connection_state(e) for a, e in (('odysseus', od), ('hermes', he)) if e},
            'auth': first.auth,
            'duplicates': found.get('odysseus_duplicates', 0) + found.get('hermes_duplicates', 0),
            'portable': portable,
            'share_enabled': shared_opt_in,
            'sync': sync_status(identity, od, he, portable, shared_opt_in, state),
        })
    return rows


def sync_status(identity, od, he, portable, opted_in, state):
    if not portable:
        reason = (od or he)
        if reason.auth == AUTH_OAUTH:
            return 'not_shareable_credential'
        return 'not_shareable_protocol' if reason.reason == 'plugin' else 'not_shareable'
    if od and he:
        return 'in_sync' if od.definition_fingerprint() == he.definition_fingerprint() or set(od.models) == set(he.models) else 'models_differ'
    missing = 'hermes' if od else 'odysseus'
    tomb = state.tombstones.get(identity)
    if tomb:
        return 'deleted_on_' + tomb.get('by', 'one_side')
    if not opted_in:
        return 'sharing_off'
    return 'pending_' + missing

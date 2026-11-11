"""Optional official Hermes Gateway + Webapp component: pinned source, the official Webapp build, ownership-aware lifecycle.

Separate from src/hermes_component.py (the WebUI): its own config file (<root>/managed/webapp/component.json), its own lock and its own
release folders. Nothing here reads or writes the WebUI component or its state, so the existing install and its rollback are unaffected.

Ownership
  managed  Odysseus provisioned it: a pinned clone, a runtime, the built Webapp bundle and a gateway home it created. Odysseus may start,
           stop, upgrade and roll it back, and only ever touches what it created.
  adopted  The user's existing Hermes. Read-only: the checkout, the runtime and the home are verified and recorded, never written. The
           bundle (optional) is built from a pinned `git archive` export into a release folder and served with HERMES_WEBAPP_DIST, the
           switch upstream documents for a prebuilt bundle, so the user's checkout is not touched. Odysseus never starts, stops or edits it.

Sources are pinned to one immutable full commit (config/hermes-component.lock.json, key "webapp"). Node and pnpm minimums come from the
pinned checkout's own package.json "engines", so the official build's requirements are not duplicated here.
"""
import hashlib
import http.client
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

from src.hermes_component import (ComponentError, SourcePin, atomic_bytes, atomic_json, checkout_source, component_lock, patch_state,
                                  private_directory, probe_python, run_install, safe_directory, verify_checkout)

ORIGINS = ('managed', 'adopted')
PLUGIN_NAME = 'odysseus-return'
BUILD_SCRIPT = 'apps/desktop/scripts/build-webapp.mjs'
BUNDLE_RELATIVE = 'apps/desktop/dist-webapp'
ROUTE_GUARD_PATCH = 'integrations/hermes-mode/patches/webapp-route-guard.patch'
_NO_WINDOW = getattr(subprocess, 'CREATE_NO_WINDOW', 0)
_RELEASE = re.compile(r'[0-9a-f]{12}')
_SECRET_NAME = re.compile(r'KEY|TOKEN|SECRET|PASSWORD|CREDENTIAL', re.I)
# What the gateway process is given. Odysseus' own environment (provider keys, session secrets) is NOT passed on: the agent's shell inherits this.
_GATEWAY_ENV = ('SystemRoot', 'SYSTEMROOT', 'SystemDrive', 'windir', 'ComSpec', 'PATHEXT', 'PATH', 'OS', 'USERNAME', 'USERDOMAIN', 'COMPUTERNAME',
                'USERPROFILE', 'HOMEDRIVE', 'HOMEPATH', 'APPDATA', 'LOCALAPPDATA', 'ProgramData', 'ProgramFiles', 'ProgramFiles(x86)',
                'ProgramW6432', 'CommonProgramFiles', 'CommonProgramFiles(x86)', 'CommonProgramW6432', 'PROCESSOR_ARCHITECTURE',
                'NUMBER_OF_PROCESSORS', 'LANG', 'LC_ALL', 'TZ')


# ── pin ──────────────────────────────────────────────────────────────────────────────────────
def load_webapp_pin(application_root):
    """The pinned official source for the gateway and the Webapp: one approved repository, one immutable full commit."""
    try:
        data = json.loads((Path(application_root) / 'config/hermes-component.lock.json').read_text(encoding='utf-8'))
        entry = data['webapp']
        return SourcePin('agent', entry['repository'], entry['revision'])
    except (OSError, KeyError, TypeError, ValueError) as exc:
        raise ComponentError('The Hermes Webapp pin is missing or invalid.') from exc


def loopback_port(url):
    parts = urlsplit(url)
    try:
        port = parts.port
    except ValueError:
        port = None
    if parts.scheme != 'http' or parts.hostname != '127.0.0.1' or not port or url != f'http://127.0.0.1:{port}/':
        raise ComponentError('The Webapp URL must be exactly http://127.0.0.1:PORT/.')
    return port


# ── configuration file ────────────────────────────────────────────────────────────────────
def read_webapp_config(path):
    path = Path(path)
    if not path.exists():
        return None
    try:
        if path.stat().st_size > 65536:
            raise ValueError('oversized')
        data = json.loads(path.read_text(encoding='utf-8'))
        if (not isinstance(data, dict) or type(data.get('version')) is not int or data['version'] != 1 or data.get('kind') != 'webapp'
                or type(data.get('enabled')) is not bool or data.get('origin') not in ORIGINS):
            raise ValueError('schema')
        for key in ('url', 'home', 'python', 'agent'):
            if not isinstance(data.get(key), str) or not data[key]:
                raise ValueError(key)
        loopback_port(data['url'])
        for key in ('release', 'previous'):
            if data.get(key) is not None and not (isinstance(data[key], str) and _RELEASE.fullmatch(data[key])):
                raise ValueError(key)
        if data.get('bundle') is not None and not isinstance(data['bundle'], str):
            raise ValueError('bundle')
        return data
    except (OSError, ValueError, RecursionError, ComponentError) as exc:
        raise ComponentError('Webapp component configuration is unreadable or unsupported; it was not replaced.') from exc


# ── helpers ───────────────────────────────────────────────────────────────────────────────
@contextmanager
def read_only_git():
    """`git status` normally refreshes the index; against a checkout we only inspect, that is still a write."""
    previous = os.environ.get('GIT_OPTIONAL_LOCKS')
    os.environ['GIT_OPTIONAL_LOCKS'] = '0'
    try:
        yield
    finally:
        if previous is None:
            os.environ.pop('GIT_OPTIONAL_LOCKS', None)
        else:
            os.environ['GIT_OPTIONAL_LOCKS'] = previous


@dataclass(frozen=True)
class Tool:
    name: str
    executable: str
    directory: str
    version: str


def _version_key(text):
    return tuple(int(part) for part in re.findall(r'\d+', text)[:4])


def find_tool(name, minimum_major, environment):
    """The newest Node or pnpm at or above ``minimum_major``. ODYSSEUS_HERMES_<NAME>_DIR is exclusive when set; otherwise the Hermes
    tools folder (%LOCALAPPDATA%\\hermes\\tools) is searched newest first, then PATH. None when nothing qualifies."""
    explicit = environment.get(f'ODYSSEUS_HERMES_{name.upper()}_DIR')
    candidates = []
    if explicit:
        candidates.append(Path(explicit))
    else:
        local = environment.get('LOCALAPPDATA')
        tools = Path(local) / 'hermes' / 'tools' if local else None
        if tools and tools.is_dir():
            found = [item for item in tools.glob(name + '-*') if item.is_dir()]
            candidates += sorted(found, key=lambda item: _version_key(item.name), reverse=True)
        on_path = shutil.which(name, path=environment.get('PATH'))
        if on_path:
            candidates.append(Path(on_path).parent)
    for directory in candidates:
        executable = shutil.which(name, path=str(directory))
        if not executable:
            continue
        try:
            result = subprocess.run([executable, '--version'], capture_output=True, text=True, timeout=30, creationflags=_NO_WINDOW,
                                    env={k: v for k, v in environment.items() if not _SECRET_NAME.search(k)})
        except (OSError, subprocess.TimeoutExpired):
            continue
        match = re.search(r'\d+\.\d+\.\d+', result.stdout)
        if result.returncode == 0 and match and int(match.group(0).split('.')[0]) >= minimum_major:
            return Tool(name, executable, str(directory), match.group(0))
    return None


def engine_majors(source):
    """Minimum Node and pnpm major versions the pinned checkout itself declares (package.json "engines")."""
    try:
        engines = json.loads((Path(source) / 'package.json').read_text(encoding='utf-8'))['engines']
        majors = {name: int(re.search(r'\d+', str(engines[name])).group(0)) for name in ('node', 'pnpm')}
    except (OSError, KeyError, TypeError, ValueError, AttributeError) as exc:
        raise ComponentError('The pinned Hermes source does not declare its Node and pnpm requirements.') from exc
    return majors


def build_environment(base, tools, directory):
    """Environment for the official Webapp build: toolchain first on PATH, caches and temp inside the component, no Hermes/Odysseus
    variables and nothing credential-shaped."""
    env = {key: value for key, value in base.items()
           if not key.upper().startswith(('HERMES_', 'ODYSSEUS_', 'PYTHON', 'NODE_', 'NPM_', 'PNPM_')) and not _SECRET_NAME.search(key)}
    cache, temp = Path(directory) / 'cache', Path(directory) / 'tmp'
    for folder in (cache, temp):
        folder.mkdir(parents=True, exist_ok=True)
    env['PATH'] = os.pathsep.join([tool.directory for tool in tools] + [env.get('PATH', '')])
    env.update({'pnpm_config_store_dir': str(cache / 'pnpm-store'), 'npm_config_cache': str(cache / 'npm'),
                'TEMP': str(temp), 'TMP': str(temp), 'CI': '1'})
    return env


def inspect_bundle(bundle):
    """Whether ``bundle`` is a complete, unmarked official Webapp bundle with a build receipt."""
    bundle, problems, receipt_sha = Path(bundle), [], None
    if not (bundle / 'index.html').is_file():
        problems.append('index.html is missing')
    try:
        raw = (bundle / 'hermes-build.json').read_bytes()
        data = json.loads(raw)
        receipt_sha = hashlib.sha256(raw).hexdigest()
        if not isinstance(data, dict) or data.get('schema') != 1 or data.get('product') != 'webapp':
            problems.append('the build receipt is not a Webapp receipt')
    except (OSError, ValueError):
        problems.append('the build receipt is missing or unreadable')
    if (bundle / '.hermes-stale').exists():
        problems.append('the bundle is marked stale')
    return {'ok': not problems, 'problems': problems, 'receipt_sha256': receipt_sha}


def export_source(checkout, pin, target, environment):
    """A clean copy of the pinned revision from ``checkout`` (read-only on the checkout), extracted with tar's `data` filter."""
    git = shutil.which('git')
    if not git:
        raise ComponentError('Git for Windows is required to export the pinned Hermes source.')
    target = Path(target)
    if target.exists():
        raise ComponentError('A source destination already exists; it will not be replaced.')
    if not hasattr(tarfile, 'data_filter'):
        raise ComponentError('This Python cannot extract archives safely; use Python 3.12 or newer.')
    archive = target.with_name(target.name + '.tar')
    env = {**environment, 'GIT_OPTIONAL_LOCKS': '0', 'GIT_TERMINAL_PROMPT': '0'}
    try:
        with archive.open('wb') as out:
            result = subprocess.run([git, '-C', str(checkout), 'archive', '--format=tar', pin.revision], stdout=out, stderr=subprocess.PIPE,
                                    timeout=900, env=env, creationflags=_NO_WINDOW)
        if result.returncode:
            raise ComponentError('The pinned Hermes revision could not be exported from the existing checkout.')
        target.mkdir(parents=True)
        with tarfile.open(archive) as tar:
            tar.extractall(target, filter='data')
    except (OSError, tarfile.TarError, subprocess.TimeoutExpired) as exc:
        raise ComponentError('The pinned Hermes source could not be exported.') from exc
    finally:
        archive.unlink(missing_ok=True)


_READ_SETTINGS = r'''
import json, sys
from pathlib import Path
from ruamel.yaml import YAML
p = Path(sys.argv[1]) / "config.yaml"
d = (YAML(typ="safe").load(p.read_text(encoding="utf-8")) or {}) if p.is_file() else {}
w = d.get("webapp") if isinstance(d.get("webapp"), dict) else {}
dk = d.get("desktop") if isinstance(d.get("desktop"), dict) else {}
ap = d.get("approvals") if isinstance(d.get("approvals"), dict) else {}
print(json.dumps({"config_present": p.is_file(), "webapp": {k: w.get(k) for k in ("enabled", "host", "port", "public_url")},
  "approvals_mode": ap.get("mode"), "repo_scan_enabled": dk.get("repo_scan_enabled"), "repo_scan_roots": len(dk.get("repo_scan_roots") or []),
  "sign_in_gate_configured": bool((d.get("dashboard") or {}).get("basic_auth")) if isinstance(d.get("dashboard"), dict) else False,
  "plugins_enabled": [x for x in ((d.get("plugins") or {}).get("enabled") or []) if isinstance(x, str)] if isinstance(d.get("plugins"), dict) else []}))
'''

_WRITE_SETTINGS = r'''
import json, os, sys
from ruamel.yaml import YAML
path, payload = sys.argv[1], json.loads(sys.argv[2])
y = YAML(); y.preserve_quotes = True
with open(path, encoding="utf-8") as f:
    data = y.load(f) or {}
for section, values in payload.items():
    if data.get(section) is None:
        data[section] = {}
    for k, v in values.items():
        data[section][k] = v
tmp = path + ".odysseus.tmp"
with open(tmp, "w", encoding="utf-8", newline="") as f:
    y.dump(data, f)
os.replace(tmp, path)
'''


def _yaml_scalar(value):
    return json.dumps(value) if isinstance(value, (str, list)) else ('true' if value is True else 'false' if value is False else str(value))


# ── component ─────────────────────────────────────────────────────────────────────────────
class WebappComponent:
    def __init__(self, root, application_root=None, environment=None):
        self.root = safe_directory(root)
        self.application_root = Path(application_root).resolve() if application_root else Path(__file__).resolve().parents[1]
        if application_root and self.root.is_relative_to(self.application_root):
            raise ComponentError('Persistent Hermes component state must be outside the Odysseus source checkout.')
        self.environment = dict(os.environ if environment is None else environment)
        self.directory = self.root / 'managed' / 'webapp'
        self.config_path = self.directory / 'component.json'

    # -- reading ---------------------------------------------------------------------------
    def config(self):
        return read_webapp_config(self.config_path)

    def _release_dir(self, release):
        return self.directory / 'releases' / release

    def _hermes_yaml(self, python, code, *args):
        try:
            result = subprocess.run([str(python), '-I', '-B', '-c', code, *args], capture_output=True, text=True, timeout=60, creationflags=_NO_WINDOW)
            if result.returncode:
                raise ValueError('yaml helper')
            return result.stdout.strip()
        except (OSError, ValueError, subprocess.TimeoutExpired) as exc:
            raise ComponentError('The Hermes interpreter could not read or write its configuration.') from exc

    def home_settings(self, python, home):
        """What the home's config.yaml says about the Webapp. Values only for non-secret settings; the sign-in secret is reported as present or not."""
        try:
            return json.loads(self._hermes_yaml(python, _READ_SETTINGS, str(home)))
        except ValueError as exc:
            raise ComponentError('The Hermes interpreter returned an unreadable configuration summary.') from exc

    # -- plan (read-only) -----------------------------------------------------------------
    def plan(self, agent=None, home=None, python=None, url=None):
        """Everything `ensure` would rely on or do, with no writes anywhere. Safe against a live, user-owned Hermes."""
        pin, steps, report = load_webapp_pin(self.application_root), [], {'writes_performed': False}
        report['pin'] = {'repository': pin.repository, 'revision': pin.revision}
        local = self.environment.get('LOCALAPPDATA')
        default_home = Path(local) / 'hermes' if local else Path.home() / '.hermes'
        home = Path(home or self.environment.get('HERMES_HOME') or default_home)
        agent = Path(agent or self.environment.get('HERMES_WEBUI_AGENT_DIR') or home / 'hermes-agent')
        current = None
        try:
            current = self.config()
        except ComponentError as exc:
            report['current'] = {'error': str(exc)}
        if current:
            report['current'] = {key: current.get(key) for key in ('origin', 'enabled', 'release', 'previous', 'url', 'home', 'agent')}
        found = {'agent': str(agent), 'home': str(home), 'home_exists': home.is_dir()}
        with read_only_git():
            try:
                verify_checkout(agent, pin)
                found['checkout'] = 'at-pin'
            except ComponentError:
                found['checkout'] = 'differs-or-missing'
        majors = None
        if (agent / 'package.json').is_file():
            try:
                majors = engine_majors(agent)
            except ComponentError:
                majors = None
        report['engines'] = majors
        tools = {}
        for name in ('node', 'pnpm'):
            minimum = (majors or {}).get(name)
            tool = find_tool(name, minimum or 0, self.environment) if minimum else None
            tools[name] = {'version': tool.version, 'directory': tool.directory} if tool else None
        report['tools'] = tools
        runtime = python or self.environment.get('HERMES_WEBUI_PYTHON')
        if runtime:
            try:
                probe_python(runtime, require_agent=True)
                found['python'] = 'ok'
            except ComponentError as exc:
                found['python'] = str(exc)
            if found['python'] == 'ok' and home.is_dir():
                try:
                    found['settings'] = self.home_settings(runtime, home)
                except ComponentError as exc:
                    found['settings_error'] = str(exc)
        else:
            found['python'] = 'not specified'
        found['desktop_plugin'] = (home / 'desktop-plugins' / PLUGIN_NAME / 'plugin.js').is_file()
        report['adopted'] = found
        if found['checkout'] != 'at-pin':
            steps.append('The existing checkout is not at the pinned revision: it will not be modified; a managed install would clone the pin instead.')
        if not all(tools.values()):
            steps.append('Node and pnpm at the versions the pinned source requires were not found: set ODYSSEUS_HERMES_NODE_DIR / _PNPM_DIR or install them in the Hermes tools folder.')
        settings = found.get('settings') or {}
        if settings and not (settings.get('webapp') or {}).get('enabled'):
            steps.append('config.yaml does not enable webapp (a user-owned file: Odysseus will not edit it; enable it yourself).')
        if settings and settings.get('repo_scan_enabled') in (None, True) and not settings.get('repo_scan_roots'):
            steps.append('Repository discovery is on with no roots, so it scans the home folder; set desktop.repo_scan_roots or disable it.')
        if settings and settings.get('approvals_mode') != 'manual':
            steps.append(f'approvals.mode is {settings.get("approvals_mode")!r}, not manual (a user-owned decision).')
        if not found['desktop_plugin']:
            steps.append(f'The {PLUGIN_NAME} desktop plugin is not installed in this home (Odysseus will not install it into a user-owned home).')
        report['steps'] = steps
        return report

    # -- ensure ---------------------------------------------------------------------------
    def ensure(self, origin='managed', *, agent=None, home=None, python=None, port=None, public_url='', repo_roots=None, build=True,
               repair=False, enable=False):
        if origin not in ORIGINS:
            raise ComponentError('origin must be managed or adopted.')
        pin = load_webapp_pin(self.application_root)
        with component_lock(self.directory):
            config = self.config()
            if config and config['origin'] != origin:
                raise ComponentError(f'The Webapp component is {config["origin"]}; Odysseus will not switch its ownership.')
            if config and not config['enabled'] and not enable:
                return config
            if config and not repair:
                self.validate(config)
                if enable and not config['enabled']:
                    config = {**config, 'enabled': True}
                    atomic_json(self.config_path, config)
                return config
            if origin == 'adopted':
                candidate = self._ensure_adopted(pin, config, agent, home, python, port, build)
            else:
                candidate = self._ensure_managed(pin, config, home, python, port, public_url, repo_roots)
            atomic_json(self.config_path, candidate)
            return candidate

    def _tools(self, source):
        majors = engine_majors(source)
        tools = [find_tool(name, majors[name], self.environment) for name in ('node', 'pnpm')]
        if not all(tools):
            raise ComponentError(f'Node >= {majors["node"]} and pnpm >= {majors["pnpm"]} (what the pinned Hermes source requires) were not found; '
                                 'set ODYSSEUS_HERMES_NODE_DIR / ODYSSEUS_HERMES_PNPM_DIR.')
        return tools

    def _build(self, source, release_dir, log):
        tools = self._tools(source)
        env = build_environment(self.environment, tools, self.directory)
        run_install([tools[0].executable, str(Path(source) / BUILD_SCRIPT)], log, env, cwd=source)
        bundle = Path(source) / BUNDLE_RELATIVE
        verdict = inspect_bundle(bundle)
        if not verdict['ok']:
            raise ComponentError('The Webapp build did not produce a complete bundle: ' + '; '.join(verdict['problems']))
        return bundle, verdict, {tool.name: tool.version for tool in tools}

    def _write_release(self, release_dir, state, **extra):
        atomic_json(release_dir / 'release.json', {'state': state, **extra})

    def _ensure_adopted(self, pin, previous, agent, home, python, port, build):
        if not (agent and home and python and port):
            raise ComponentError('An adopted Webapp needs --agent, --home, --python and --port (the existing Hermes installation).')
        agent, home = safe_directory(agent), safe_directory(home)
        with read_only_git():
            verify_checkout(agent, pin)
        if not (agent / 'gateway' / 'run.py').is_file() or not home.is_dir():
            raise ComponentError('The existing Hermes layout is incomplete; it was not modified.')
        probe_python(python, require_agent=True)
        release = bundle = verdict = tool_versions = None
        if build:
            release = uuid.uuid4().hex[:12]
            release_dir = private_directory(self._release_dir(release))
            log = release_dir / 'build.log'
            self._write_release(release_dir, 'preparing', revision=pin.revision, origin='adopted')
            export_source(agent, pin, release_dir / 'src', self.environment)
            bundle, verdict, tool_versions = self._build(release_dir / 'src', release_dir, log)
            self._write_release(release_dir, 'ready', revision=pin.revision, origin='adopted', receipt_sha256=verdict['receipt_sha256'], tools=tool_versions)
        return {'version': 1, 'kind': 'webapp', 'enabled': True, 'origin': 'adopted', 'release': release,
                'previous': previous.get('release') if previous else None, 'agent': str(agent), 'python': str(python), 'home': str(home),
                'bundle': str(bundle) if bundle else None, 'url': f'http://127.0.0.1:{int(port)}/', 'public_url': '',
                'revisions': {'webapp': pin.revision}, 'build': {'receipt_sha256': verdict['receipt_sha256'], 'tools': tool_versions} if verdict else None}

    def _ensure_managed(self, pin, previous, home, python, port, public_url, repo_roots):
        port = int(port or (loopback_port(previous['url']) if previous else 0))
        if not port:
            raise ComponentError('A managed Webapp needs --port.')
        release = uuid.uuid4().hex[:12]
        release_dir = private_directory(self._release_dir(release))
        self._write_release(release_dir, 'preparing', revision=pin.revision, origin='managed')
        try:
            return self._provision_managed(pin, previous, release, release_dir, home, python, port, public_url, repo_roots)
        except BaseException:
            self._write_release(release_dir, 'failed', revision=pin.revision, origin='managed')      # kept for its logs; never referenced
            raise

    def _provision_managed(self, pin, previous, release, release_dir, home, python, port, public_url, repo_roots):
        log = release_dir / 'install.log'
        env = {**self.environment, 'GIT_TERMINAL_PROMPT': '0'}
        source = release_dir / 'src'
        checkout_source(source, pin, log, env, self.route_guard_patch())
        bundle, verdict, tool_versions = self._build(source, release_dir, log)
        if python:
            probe_python(python, require_agent=True)
            runtime, runtime_origin = str(python), 'external'
        else:
            seed = self.environment.get('HERMES_WEBUI_PYTHON') or sys.executable
            probe_python(seed)
            run_install([seed, '-B', '-m', 'pm.build_env', '--source', str(source), '--out', str(release_dir / 'venv'),
                         '--cache', str(self.directory / 'cache/pm')], log, {**env, 'HERMES_RUNTIME_DIR': str(self.directory / 'runtime')}, cwd=source)
            runtime = str(release_dir / ('venv/Scripts/python.exe' if os.name == 'nt' else 'venv/bin/python'))
            probe_python(runtime, require_agent=True)
            runtime_origin = 'managed'
        home_path = private_directory(home or (previous['home'] if previous else self.root / 'state/hermes-gateway'))
        self.check_desktop_plugin(home_path)                 # every conflict is found before anything in the home is written
        schema = self.config_schema_version(source)
        if public_url:
            # Upstream refuses to bind when webapp.public_url is set and no auth provider exists, and Odysseus will not invent credentials.
            existing = self.home_settings(runtime, home_path) if (home_path / 'config.yaml').is_file() else {}
            if not existing.get('sign_in_gate_configured'):
                raise ComponentError('webapp.public_url engages the gateway sign-in gate, which needs an auth provider the user configures '
                                     '(dashboard.basic_auth); none is configured in this home, so no public URL was set.')
        self._prepare_home(runtime, home_path, port, public_url or '', repo_roots, schema)
        plugin = self.install_desktop_plugin(home_path)
        self._write_release(release_dir, 'ready', revision=pin.revision, origin='managed', receipt_sha256=verdict['receipt_sha256'], tools=tool_versions)
        return {'version': 1, 'kind': 'webapp', 'enabled': True, 'origin': 'managed', 'release': release,
                'previous': previous.get('release') if previous else None, 'agent': str(source), 'python': runtime, 'python_origin': runtime_origin,
                'home': str(home_path), 'bundle': str(bundle), 'url': f'http://127.0.0.1:{port}/', 'public_url': public_url or '',
                'revisions': {'webapp': pin.revision}, 'build': {'receipt_sha256': verdict['receipt_sha256'], 'tools': tool_versions},
                'desktop_plugins': [plugin]}

    # -- gateway home (managed only) -------------------------------------------------------
    @staticmethod
    def config_schema_version(source):
        """The config schema version the pinned source itself declares; a fresh home without it is treated as ancient and warned about."""
        try:
            text = (Path(source) / 'hermes_cli' / 'config_defaults.py').read_text(encoding='utf-8')
            return int(re.search(r'"_config_version":\s*(\d+)', text).group(1))
        except (OSError, ValueError, AttributeError) as exc:
            raise ComponentError('The pinned Hermes source does not declare its configuration version.') from exc

    def _prepare_home(self, python, home, port, public_url, repo_roots, schema=None):
        """Create or update the Webapp settings of a home Odysseus owns. Only the keys below are set; everything else is kept."""
        settings = {'webapp': {'enabled': True, 'host': '127.0.0.1', 'port': port, 'public_url': public_url}}
        desktop = {'repo_scan_enabled': bool(repo_roots), 'repo_scan_roots': list(repo_roots or [])}
        config = home / 'config.yaml'
        if not config.exists():
            lines = [f'_config_version: {schema}', 'webapp:', '  enabled: true', '  host: 127.0.0.1', f'  port: {port}', f'  public_url: {json.dumps(public_url)}',
                     'approvals:', '  mode: manual', 'desktop:', f'  repo_scan_enabled: {_yaml_scalar(desktop["repo_scan_enabled"])}',
                     f'  repo_scan_roots: {json.dumps(desktop["repo_scan_roots"])}']
            atomic_bytes(config, ('\n'.join(lines) + '\n').encode('utf-8'))
            return
        backup = home / f'config.yaml.pre-odysseus-{time.strftime("%Y%m%d-%H%M%S")}'
        shutil.copy2(config, backup)
        self._hermes_yaml(python, _WRITE_SETTINGS, str(config), json.dumps(settings))

    def check_desktop_plugin(self, home):
        """Raise if installing the return plugin into ``home`` would overwrite something different. Writes nothing."""
        source = self.application_root / 'integrations/hermes-mode/desktop-plugins' / PLUGIN_NAME / 'plugin.js'
        if not source.is_file():
            raise ComponentError('The packaged desktop plugin is missing.')
        target = Path(home) / 'desktop-plugins' / PLUGIN_NAME / 'plugin.js'
        if target.exists() and target.read_bytes() != source.read_bytes():
            raise ComponentError('A different desktop plugin with this name already exists; it was not overwritten.')
        return source, target

    def install_desktop_plugin(self, home):
        """Copy the Odysseus return plugin into a managed home's desktop-plugins folder; a different existing plugin is never overwritten."""
        source, target = self.check_desktop_plugin(home)
        if not target.exists():
            atomic_bytes(target, source.read_bytes())
        return PLUGIN_NAME

    # -- validate / status -----------------------------------------------------------------
    def validate(self, config):
        pin = load_webapp_pin(self.application_root)
        try:
            agent = safe_directory(config['agent'])
            with read_only_git():
                verify_checkout(agent, pin, self.route_guard_patch() if config.get('origin') == 'managed' else None)
            if config.get('origin') == 'managed' and self.route_guard_state(config) != 'applied':
                raise ComponentError('The managed gateway source lacks the reviewed route guard; it will not be started.')
            probe_python(config['python'], require_agent=True)
            for key in ('home',):
                path = safe_directory(config[key])
                if path.is_relative_to(self.application_root):
                    raise ComponentError('Hermes user state must not be stored under the source checkout.')
            if config.get('bundle'):
                verdict = inspect_bundle(safe_directory(config['bundle']))
                if not verdict['ok']:
                    raise ComponentError('The Webapp bundle is incomplete or stale: ' + '; '.join(verdict['problems']))
                if config.get('build') and verdict['receipt_sha256'] != config['build'].get('receipt_sha256'):
                    raise ComponentError('The Webapp bundle no longer matches the build that was recorded; run repair.')
            elif config['origin'] == 'managed':
                raise ComponentError('A managed Webapp needs its bundle; run repair.')
        except (KeyError, TypeError) as exc:
            raise ComponentError('Webapp component configuration is incomplete; run repair.') from exc

    def status(self):
        config = self.config()
        if not config:
            return {'enabled': False, 'state': 'not_installed'}
        result = {'enabled': config['enabled'], 'origin': config['origin'], 'url': config['url'], 'release': config.get('release'),
                  'previous': config.get('previous'), 'revision': (config.get('revisions') or {}).get('webapp')}
        result['route_guard'] = self.route_guard_state(config)
        if not config['enabled']:
            return {**result, 'state': 'disabled'}
        port = loopback_port(config['url'])
        connection = http.client.HTTPConnection('127.0.0.1', port, timeout=2)
        try:
            connection.request('GET', '/api/health')
            response = connection.getresponse()
            body = response.read(65537)
            try:
                health = json.loads(body) if len(body) <= 65536 else None
            except ValueError:
                health = None
            identity = isinstance(health, dict) and health.get('ok') is True and isinstance(health.get('auth_required'), bool) and 'displayVersion' in health
            result['state'] = 'ready' if identity and response.status == 200 else ('unavailable' if identity else 'conflict')
            if identity:
                result['sign_in_gate'] = health['auth_required']
        except (OSError, http.client.HTTPException):
            result['state'] = 'stopped' if _port_free(port) else 'unavailable'
        finally:
            connection.close()
        return result

    def active_sessions(self):
        """The gateway's public, best-effort count of sessions active in the last five minutes (None when unreadable). It reports 0 on
        its own errors, so a 0 is not proof that nothing is running."""
        config = self.config()
        if not config:
            return None
        connection = http.client.HTTPConnection('127.0.0.1', loopback_port(config['url']), timeout=3)
        try:
            connection.request('GET', '/api/status')
            data = json.loads(connection.getresponse().read(262144))
            counts = [data.get('active_sessions')]
            for platform in (data.get('gateway_platforms') or {}).values():
                counts.append(((platform or {}).get('metrics') or {}).get('active_runs'))
            return sum(count for count in counts if isinstance(count, int) and not isinstance(count, bool))
        except (OSError, http.client.HTTPException, ValueError, AttributeError):
            return None
        finally:
            connection.close()

    # -- enable / disable / rollback ------------------------------------------------------
    def disable(self):
        with component_lock(self.directory):
            config = self.config() or {'version': 1, 'kind': 'webapp', 'enabled': True}
            atomic_json(self.config_path, {**config, 'enabled': False})

    def rollback(self):
        """Managed only: point the component at the previous release (kept side by side). The gateway must be restarted to serve it."""
        with component_lock(self.directory):
            config = self.config()
            if not config or config['origin'] != 'managed':
                raise ComponentError('Only a managed Webapp has releases to roll back to.')
            previous = config.get('previous')
            if not previous:
                raise ComponentError('There is no previous release to roll back to.')
            release_dir = self._release_dir(previous)
            try:
                record = json.loads((release_dir / 'release.json').read_text(encoding='utf-8'))
            except (OSError, ValueError) as exc:
                raise ComponentError('The previous release is missing or unreadable; nothing was changed.') from exc
            if record.get('state') != 'ready':
                raise ComponentError('The previous release is not complete; nothing was changed.')
            candidate = {**config, 'release': previous, 'previous': config['release'], 'agent': str(release_dir / 'src'),
                         'bundle': str(release_dir / 'src' / BUNDLE_RELATIVE), 'revisions': {'webapp': record['revision']},
                         'build': {'receipt_sha256': record.get('receipt_sha256'), 'tools': record.get('tools')}}
            venv = release_dir / ('venv/Scripts/python.exe' if os.name == 'nt' else 'venv/bin/python')
            if config.get('python_origin') == 'managed':
                candidate['python'] = str(venv)
            self.validate_release(candidate, record)
            atomic_json(self.config_path, candidate)
            return candidate

    def validate_release(self, candidate, record):
        verdict = inspect_bundle(candidate['bundle'])
        if not verdict['ok'] or verdict['receipt_sha256'] != record.get('receipt_sha256'):
            raise ComponentError('The previous release no longer matches its recorded build; nothing was changed.')
        probe_python(candidate['python'], require_agent=True)

    # -- gateway lifecycle (managed only) --------------------------------------------------
    def _require_managed(self):
        config = self.config()
        if not config or not config['enabled']:
            raise ComponentError('The Webapp component is not installed or is disabled.')
        if config['origin'] != 'managed':
            raise ComponentError('This gateway is user-owned (adopted); Odysseus never starts, stops or restarts it.')
        return config

    PROTECTED_PATHS = 'vault/odysseus-protected-paths.json'

    def protected_paths_file(self, config=None):
        config = config or self.config()
        if not config or config.get('origin') != 'managed':
            raise ComponentError('The blocked-folder list belongs to a managed gateway home; an adopted home is never written.')
        return Path(config['home']) / self.PROTECTED_PATHS

    def blocked_folders(self):
        """The folders the route guard refuses (the neutral list, else the retired WebUI's). Never raises on a missing file."""
        config = self.config()
        if not config:
            return []
        neutral = self.protected_paths_file(config) if config.get('origin') == 'managed' else None
        for path in (neutral, Path(config['home']) / 'vault' / 'webui-trusted-workspaces.json'):
            if path and path.is_file():
                try:
                    return [item['path'] for item in json.loads(path.read_text(encoding='utf-8')).get('blocked') or [] if isinstance(item, dict) and item.get('path')]
                except (OSError, ValueError, AttributeError):
                    raise ComponentError(f'{path.name} is unreadable; the route guard will refuse writes until it is repaired.')
        return []

    def set_blocked_folders(self, paths):
        """Write the neutral list. The first write imports the retired WebUI list, so adopting this store never loses an existing entry."""
        config = self._require_managed()
        target = self.protected_paths_file(config)
        existing = self.blocked_folders()
        wanted = []
        for item in list(existing) + list(paths):
            item = os.path.normpath(str(item))
            if item not in wanted:
                wanted.append(item)
        private_directory(target.parent)
        atomic_json(target, {'version': 1, 'blocked': [{'path': item} for item in wanted]})
        return wanted

    def remove_blocked_folder(self, path):
        config = self._require_managed()
        target = self.protected_paths_file(config)
        keep = [item for item in self.blocked_folders() if os.path.normcase(os.path.normpath(item)) != os.path.normcase(os.path.normpath(str(path)))]
        private_directory(target.parent)
        atomic_json(target, {'version': 1, 'blocked': [{'path': item} for item in keep]})
        return keep

    def route_guard_patch(self):
        path = (self.application_root / ROUTE_GUARD_PATCH).resolve()
        if not path.is_file():
            raise ComponentError('The reviewed route-guard patch is missing from this Odysseus installation.')
        return path

    def route_guard_state(self, config):
        """'applied' for a managed source carrying exactly the reviewed patch, else 'absent' (adopted gateways run the user's unpatched code)."""
        if config.get('origin') != 'managed':
            return 'absent'
        try:
            return patch_state(safe_directory(config['agent']), self.route_guard_patch())
        except (ComponentError, OSError, KeyError):
            return 'unknown'

    def gateway_environment(self, config):
        env = {key: self.environment[key] for key in _GATEWAY_ENV if key in self.environment}
        if 'HERMES_WRITE_SAFE_ROOT' in self.environment:      # the operator's own write policy, passed through unchanged, never invented
            env['HERMES_WRITE_SAFE_ROOT'] = self.environment['HERMES_WRITE_SAFE_ROOT']
        if 'HERMES_WEBAPP_ALLOW_GIT_PUBLISH' in self.environment:       # the operator's explicit opt-in for push / pull request, never invented
            env['HERMES_WEBAPP_ALLOW_GIT_PUBLISH'] = self.environment['HERMES_WEBAPP_ALLOW_GIT_PUBLISH']
        env.update({'HERMES_HOME': config['home'], 'PYTHONPATH': config['agent'], 'HERMES_WEBAPP_DIST': config['bundle'],
                    'PYTHONUTF8': '1', 'PYTHONDONTWRITEBYTECODE': '1'})
        return env

    def start(self, wait=True, timeout=180):
        config = self._require_managed()
        with component_lock(self.directory / 'service'):
            self.validate(config)
            state = self.status()['state']
            if state == 'ready':
                return self.status()
            if state != 'stopped':
                raise ComponentError('The Webapp port is occupied or unhealthy; no takeover attempted.')
            logs = private_directory(self.directory / 'logs')
            with (logs / 'gateway.log').open('ab') as log:
                flags = _NO_WINDOW | getattr(subprocess, 'CREATE_NEW_PROCESS_GROUP', 0)
                process = subprocess.Popen([config['python'], '-W', 'ignore', '-m', 'gateway.run'], cwd=config['agent'], env=self.gateway_environment(config),
                                           stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT, creationflags=flags)
        if not wait:
            return {**self.status(), 'state': 'starting'}
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if process.poll() is not None:
                raise ComponentError('The gateway exited while starting; see managed/webapp/logs/gateway.log.')
            if self.status()['state'] == 'ready':
                return self.status()
            time.sleep(1)
        raise ComponentError('The gateway did not become ready in time; it was left running for inspection.')

    def owned_listener(self, config):
        """The pid of the gateway Odysseus provisioned, proven by the gateway's own pid record (kind, home and entry file), or an error."""
        from src import service_controls as controls
        port = loopback_port(config['url'])
        pids, loopback_only = controls.listening_pids(port)
        if len(pids) != 1 or not loopback_only:
            raise controls.Unavailable('The Webapp port is not owned by exactly one loopback-only process; nothing was stopped.')
        pid = next(iter(pids))
        try:
            record = json.loads((Path(config['home']) / 'gateway.pid').read_text(encoding='utf-8'))
        except (OSError, ValueError) as exc:
            raise controls.Unavailable("The gateway's pid record is unreadable; its identity cannot be confirmed, nothing was stopped.") from exc
        norm = lambda value: os.path.normcase(os.path.abspath(str(value)))
        expected = norm(Path(config['agent']) / 'gateway' / 'run.py')
        argv = record.get('argv') if isinstance(record.get('argv'), list) else []
        if (record.get('kind') != 'hermes-gateway' or record.get('pid') != pid or norm(record.get('hermes_home', '')) != norm(config['home'])
                or not argv or norm(argv[0]) != expected):
            raise controls.Unavailable('The listener is not the gateway Odysseus provisioned; nothing was stopped.')
        return pid

    def stop(self, force=False):
        from src import service_controls as controls
        config = self._require_managed()
        with component_lock(self.directory / 'service'):
            if self.status()['state'] in ('stopped',):
                return self.status()
            sessions = self.active_sessions()
            if (sessions is None or sessions > 0) and not force:
                raise controls.ActiveRuns(sessions if sessions else 1, recent=True)
            controls.kill_tree(self.owned_listener(config))
            port = loopback_port(config['url'])
            deadline = time.monotonic() + 30
            while not _port_free(port) and time.monotonic() < deadline:
                time.sleep(0.3)
            if not _port_free(port):
                raise ComponentError('The gateway did not release its port; check it manually.')
        return self.status()

    def restart(self, force=False):
        self.stop(force=force)
        return self.start()


def _port_free(port):
    import socket
    try:
        with socket.socket() as sock:
            if hasattr(socket, 'SO_EXCLUSIVEADDRUSE'):
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
            sock.bind(('127.0.0.1', port))
        return True
    except OSError:
        return False

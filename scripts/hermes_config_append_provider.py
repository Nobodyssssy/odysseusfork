"""Append-only Hermes provider writer. Runs under Hermes's own interpreter.

usage: hermes_config_append_provider.py CONFIG BACKUP_DIR   (JSON request on stdin)

Appends ONE `providers: "custom:<slug>"` record (name, base_url, models) at the END of
config.yaml. Existing bytes are never rewritten: the new file must start with the exact
old bytes, parse to the old data plus the one record, and keep every comment. Never writes
a key, never touches `model`, `custom_providers` or any other record. Refuses (changing
nothing) if the endpoint already exists anywhere, or if `providers` exists but is not the
last top-level section. Exit 0 = added, 3 = already present, 1 = refused.
"""
import json
import os
from pathlib import Path
import re
import shutil
import sys
import tempfile
import time
from urllib.parse import urlsplit

from ruamel.yaml import YAML

SLUG = re.compile(r'^[a-z0-9][a-z0-9-]{0,62}$')


def canonical(url):
    try:
        p = urlsplit(str(url).strip())
        host = (p.hostname or '').lower()
        if host in ('localhost', '::1', '0.0.0.0'):
            host = '127.0.0.1'
        port = p.port
    except ValueError:
        return None
    if p.scheme not in ('http', 'https') or not host or p.username or p.password:
        return None
    if port and (p.scheme, port) in (('http', 80), ('https', 443)):
        port = None
    path = p.path.rstrip('/')
    for suffix in ('/chat/completions', '/completions', '/models'):
        if path.endswith(suffix):
            path = path[:-len(suffix)].rstrip('/')
    return f'{p.scheme}://{host}{":" + str(port) if port else ""}{path}'


def refuse(message, code=1):
    print(message, file=sys.stderr)
    raise SystemExit(code)


def plain(value):
    return json.loads(json.dumps(value, default=str))


def main():
    config_path, backup_dir = Path(sys.argv[1]), Path(sys.argv[2])
    request = json.loads(sys.stdin.read(65536))
    slug, name, url, models = request['slug'], request['name'], request['base_url'], request['models']
    if not SLUG.match(slug) or not isinstance(name, str) or not isinstance(models, list) or len(models) > 500 \
            or not all(isinstance(m, str) and 0 < len(m) <= 256 for m in models):
        refuse('invalid provider request')
    target = canonical(url)
    if not target or set(request) - {'slug', 'name', 'base_url', 'models'}:
        refuse('invalid or credential-bearing request')
    raw = config_path.read_bytes()
    loader = YAML(typ='safe')
    old = loader.load(raw.decode('utf-8'))
    if not isinstance(old, dict):
        refuse('config.yaml is not a mapping')
    providers = old.get('providers')
    if providers is not None and not isinstance(providers, dict):
        refuse('providers is not a mapping; nothing was changed')
    key = 'custom:' + slug
    records = list((providers or {}).values()) + list(old.get('custom_providers') or [])
    if isinstance(old.get('model'), dict):
        records.append(old['model'])
    seen = [canonical(r[f]) for r in records if isinstance(r, dict) for f in ('base_url', 'api', 'url') if r.get(f)]
    if key in (providers or {}) or target in seen:
        print('already present')
        raise SystemExit(3)
    entry = f'  {json.dumps(key)}:\n    name: {json.dumps(name[:120])}\n    base_url: {json.dumps(url)}\n'
    entry += ('    models:\n' + ''.join(f'      - {json.dumps(m)}\n' for m in models)) if models else '    models: []\n'
    addition = ''
    if providers is None:
        addition = 'providers:\n' + entry
    elif list(old)[-1] == 'providers':
        addition = entry
    else:
        refuse('providers exists but is not the last section; add this provider manually (nothing was changed)')
    new_bytes = raw + (b'' if raw.endswith(b'\n') else b'\n') + addition.encode('utf-8')
    new = loader.load(new_bytes.decode('utf-8'))
    expected = plain(old)
    expected.setdefault('providers', {})[key] = {'name': name[:120], 'base_url': url, 'models': list(models)}
    if not new_bytes.startswith(raw) or plain(new) != expected:
        refuse('verification failed: the file would change beyond the one added record; nothing was written')
    backup_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(config_path, backup_dir / f'config-{time.strftime("%Y%m%d-%H%M%S")}-{os.getpid()}-{time.monotonic_ns() % 100000}.yaml')
    with tempfile.NamedTemporaryFile('wb', delete=False, dir=config_path.parent, suffix='.tmp') as stream:
        tmp = Path(stream.name)
        stream.write(new_bytes)
        stream.flush()
        os.fsync(stream.fileno())
    try:
        os.replace(tmp, config_path)
    finally:
        tmp.unlink(missing_ok=True)
    print('added')


if __name__ == '__main__':
    main()

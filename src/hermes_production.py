"""The one production setting that switches Projects to the managed official Webapp, and the checks that say whether production is ready for it.

The setting is a single line in the production ``.env`` (loaded by app.py before the mode service is built):

    ODYSSEUS_HERMES_BACKEND=webapp

Without it Odysseus behaves exactly as before (the old WebUI proxy), so removing the line and restarting Odysseus is the rollback. This module only edits
that one line, always after keeping a copy of the file, and never touches anything else in it.
"""
import hashlib
import json
import os
import re
import shutil
import time
from pathlib import Path

SETTING = 'ODYSSEUS_HERMES_BACKEND'
VALUE = 'webapp'
_LINE = re.compile(rf'^\s*(?:export\s+)?{SETTING}\s*=.*$')
_BOM = b'\xef\xbb\xbf'


class ProductionError(RuntimeError):
    pass


def _read(path):
    raw = Path(path).read_bytes()
    bom = raw.startswith(_BOM)
    text = raw[len(_BOM):].decode('utf-8') if bom else raw.decode('utf-8')
    newline = '\r\n' if '\r\n' in text else '\n'
    return bom, newline, text.replace('\r\n', '\n').split('\n')


def _write(path, bom, newline, lines):
    data = newline.join(lines).encode('utf-8')
    tmp = Path(str(path) + '.switching')
    tmp.write_bytes((_BOM if bom else b'') + data)
    os.replace(tmp, path)


def current(path):
    """The value of the setting in the file, or None."""
    if not Path(path).is_file():
        return None
    for line in _read(path)[2]:
        if _LINE.match(line):
            return line.split('=', 1)[1].strip().strip('"\'')
    return None


def switch(path, on, *, keep=True):
    """Turn the setting on (``on=True``) or remove it. Returns what was done. A copy of the file is kept next to it first."""
    path = Path(path)
    if not path.is_file():
        raise ProductionError(f'{path} does not exist; nothing was changed.')
    bom, newline, lines = _read(path)
    wanted = f'{SETTING}={VALUE}'
    existing = [index for index, line in enumerate(lines) if _LINE.match(line)]
    if on:
        if len(existing) == 1 and lines[existing[0]].strip() == wanted:
            return {'changed': False, 'copy': None, 'detail': 'already on'}
        updated = [line for index, line in enumerate(lines) if index not in existing]
        while updated and updated[-1] == '':
            updated.pop()
        updated += [wanted, '']
    else:
        if not existing:
            return {'changed': False, 'copy': None, 'detail': 'already off'}
        updated = [line for index, line in enumerate(lines) if index not in existing]
    copy = None
    if keep:
        copy = path.with_name(f'{path.name}.pre-projects-{time.strftime("%Y%m%d-%H%M%S")}')
        shutil.copy2(path, copy)
    _write(path, bom, newline, updated)
    return {'changed': True, 'copy': str(copy) if copy else None, 'detail': 'on' if on else 'removed'}


def verify_wheel(pins_path, directory=None):
    """(ok, detail): the staged websockets wheel exists and matches the pinned hash."""
    pins = json.loads(Path(pins_path).read_text(encoding='utf-8'))['websockets']
    folder = Path(directory or pins['staged_in'])
    wheel = folder / pins['wheel']
    if not wheel.is_file():
        return False, f'{wheel} is missing'
    digest = hashlib.sha256(wheel.read_bytes()).hexdigest()
    return digest == pins['sha256'], f"{pins['wheel']} sha256 {'matches' if digest == pins['sha256'] else 'DIFFERS from'} the pin"

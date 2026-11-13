"""The cache scan must not stall when no Ollama is listening.

A silent host firewall drops localhost SYNs instead of refusing them, so the
old probe paid its full 2 s timeout for 127.0.0.1 and *again* for `localhost`
(which resolves to both ::1 and 127.0.0.1) — roughly 4 s of pure waiting on
every /api/model/cached call on a machine with no Ollama. A listening Ollama
must still be discovered, and an incomplete download must still be reported as
incomplete.
"""
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

from routes.cookbook_helpers import _cached_model_scan_script


def _run_scan(script: str, env: dict) -> tuple[list[dict], float]:
    fd, name = tempfile.mkstemp(suffix='.py')
    path = Path(name)
    os.write(fd, script.encode('utf-8'))
    os.close(fd)
    try:
        start = time.perf_counter()
        proc = subprocess.run([sys.executable, str(path)], capture_output=True,
                              text=True, env=env, timeout=60)
        elapsed = time.perf_counter() - start
        assert proc.returncode == 0, proc.stderr[-500:]
        return json.loads(proc.stdout), elapsed
    finally:
        path.unlink(missing_ok=True)


def _clean_env(**extra) -> dict:
    env = dict(os.environ)
    for key in ('HF_HOME', 'HUGGINGFACE_HUB_CACHE', 'HF_HUB_CACHE'):
        env.pop(key, None)
    env.update(extra)
    return env


def test_scan_completes_quickly_when_nothing_listens_on_ollama_port():
    """No listener on 11434 -> the scan must not pay the HTTP probe timeouts."""
    env = _clean_env(ODYSSEUS_OLLAMA_SCAN_URL='http://127.0.0.1:11434')
    # The probe port is asserted closed by the caller's environment; nothing
    # listens on 11434 on the test host, so the connect fails fast.
    _, elapsed = _run_scan(_cached_model_scan_script(), env)
    assert elapsed < 2.0, f'scan took {elapsed:.2f}s with no Ollama listener'


def test_reachable_ollama_is_still_discovered():
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            body = json.dumps({'models': [
                {'name': 'fixture/ollama-model:latest', 'size': 2147483648},
            ]}).encode('utf-8')
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    server = HTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        env = _clean_env(ODYSSEUS_OLLAMA_SCAN_URL=f'http://127.0.0.1:{server.server_address[1]}')
        models, _ = _run_scan(_cached_model_scan_script(), env)
    finally:
        server.shutdown()
    ollama = [m for m in models if m.get('backend') == 'ollama']
    assert [m['repo_id'] for m in ollama] == ['fixture/ollama-model:latest']


def test_port_probe_does_not_report_a_closed_port_as_reachable():
    """A port probe that cannot connect must not fabricate an Ollama entry."""
    env = _clean_env(ODYSSEUS_OLLAMA_SCAN_URL='http://127.0.0.1:1')  # tcpmux, closed
    models, _ = _run_scan(_cached_model_scan_script(), env)
    assert all(m.get('backend') != 'ollama' for m in models)


def test_scan_still_reports_an_incomplete_download(tmp_path):
    """The probe change must not affect blob/incomplete classification."""
    cache = tmp_path / 'hub'
    model = cache / 'models--fixture--partial'
    blobs = model / 'blobs'
    blobs.mkdir(parents=True)
    (blobs / 'weights.safetensors.incomplete').write_bytes(b'partial')
    snap = model / 'snapshots' / 'rev1'
    snap.mkdir(parents=True)
    (snap / 'config.json').write_text('{}')

    env = _clean_env(HF_HOME=str(tmp_path))
    models, _ = _run_scan(_cached_model_scan_script(), env)
    row = next(m for m in models if m['repo_id'] == 'fixture/partial')
    # A partial blob is reported as incomplete, never as a usable model: the
    # endpoint maps has_incomplete -> status "downloading".
    assert row['has_incomplete'] is True
    assert row['nb_files'] == 1


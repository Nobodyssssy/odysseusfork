"""A live pid with no proven identity must not be signalled.

The first identity work recorded a creation time at launch and compared it at
stop time. Tasks launched before that existed have no token, and the previous
fallback killed on the bare pid alone — which is unsafe once Windows has
recycled the number. Ownership is now tri-state: owned / dead / recycled /
unverifiable. The stop path refuses to signal anything it cannot prove is ours,
and the poller reports `unverifiable` instead of claiming running or stopped.
"""
import json
import subprocess
from pathlib import Path
import sys
import time

import httpx
import pytest
from fastapi import FastAPI

import routes.cookbook_routes as routes
from core.middleware import INTERNAL_TOOL_HEADER, INTERNAL_TOOL_TOKEN
from src import process_ownership


def _app(monkeypatch, tmp_path):
    monkeypatch.setattr(routes, "_COOKBOOK_STATE_PATH", tmp_path / "cookbook_state.json")
    monkeypatch.setattr(routes, "TMUX_LOG_DIR", tmp_path)
    app = FastAPI()
    app.include_router(routes.setup_cookbook_routes())
    return app


def _client(app):
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test",
                             headers={INTERNAL_TOOL_HEADER: INTERNAL_TOOL_TOKEN})


def _state(tmp_path, status="running"):
    return {
        "tasks": [{
            "sessionId": "serve-legacy1", "type": "download", "status": status,
            "modelId": "org/model", "remoteHost": "", "platform": "windows",
            "output": "", "payload": {"repo_id": "org/model"},
        }],
        "env": {"servers": []},
    }


def _write(tmp_path, status="running"):
    (tmp_path / "cookbook_state.json").write_text(json.dumps(_state(tmp_path, status)), encoding="utf-8")


# -- tri-state probe ---------------------------------------------------------

def test_state_is_dead_for_a_missing_pid():
    assert routes._local_pid_state(None, "serve-x") == "dead"


def test_state_is_unverifiable_without_a_recorded_token(tmp_path, monkeypatch):
    monkeypatch.setattr(routes, "TMUX_LOG_DIR", tmp_path)
    proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(20)"])
    try:
        assert routes._local_pid_state(proc.pid, "serve-legacy") == "unverifiable"
    finally:
        proc.kill()
        proc.wait(timeout=10)


def test_state_is_recycled_when_the_token_belongs_to_another_process(tmp_path, monkeypatch):
    monkeypatch.setattr(routes, "TMUX_LOG_DIR", tmp_path)
    a = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(20)"])
    b = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(20)"])
    try:
        (tmp_path / "serve-b.pid.token").write_text(process_ownership.start_token(a.pid), encoding="utf-8")
        assert routes._local_pid_state(b.pid, "serve-b") == "recycled"
    finally:
        for proc in (a, b):
            proc.kill()
            proc.wait(timeout=10)


def test_state_is_owned_when_the_token_matches(tmp_path, monkeypatch):
    monkeypatch.setattr(routes, "TMUX_LOG_DIR", tmp_path)
    proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(20)"])
    try:
        (tmp_path / "serve-ok.pid.token").write_text(process_ownership.start_token(proc.pid), encoding="utf-8")
        assert routes._local_pid_state(proc.pid, "serve-ok") == "owned"
    finally:
        proc.kill()
        proc.wait(timeout=10)


# -- poller reports rather than claims --------------------------------------

@pytest.mark.asyncio
async def test_poller_reports_unverifiable_for_a_legacy_task(monkeypatch, tmp_path):
    proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(20)"])
    try:
        app = _app(monkeypatch, tmp_path)
        _write(tmp_path, "running")
        (tmp_path / "serve-legacy1.pid").write_text(str(proc.pid), encoding="utf-8")
        async with _client(app) as client:
            data = (await client.get("/api/cookbook/tasks/status")).json()
        row = next(t for t in data["tasks"] if t["session_id"] == "serve-legacy1")
        assert row["status"] == "unverifiable"
    finally:
        proc.kill()
        proc.wait(timeout=10)


@pytest.mark.asyncio
async def test_poller_reports_unverifiable_for_a_recycled_pid(monkeypatch, tmp_path):
    proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(20)"])
    try:
        app = _app(monkeypatch, tmp_path)
        _write(tmp_path, "running")
        (tmp_path / "serve-legacy1.pid").write_text(str(proc.pid), encoding="utf-8")
        (tmp_path / "serve-legacy1.pid.token").write_text("win32:1", encoding="utf-8")
        async with _client(app) as client:
            data = (await client.get("/api/cookbook/tasks/status")).json()
        row = next(t for t in data["tasks"] if t["session_id"] == "serve-legacy1")
        assert row["status"] == "unverifiable"
    finally:
        proc.kill()
        proc.wait(timeout=10)


# -- stop path ---------------------------------------------------------------

def test_stop_command_refuses_to_signal_without_a_token():
    source = (Path(__file__).resolve().parents[1] / "static" / "js" / "cookbookRunning.js").read_text(encoding="utf-8")
    start = source.index("function _winPidStopTreePs")
    end = source.index("\n}", start)
    body = source[start:end]
    assert "if (-not $t) { Write-Output 'UNVERIFIED'; exit 0 }" in body
    assert "if ($t -notmatch '^win32:(\\\\d+)$') { Write-Output 'UNVERIFIED'; exit 0 }" in body
    assert "Write-Output 'RECYCLED'; exit 0" in body
    # The bare-pid fallback must be gone: the kill is reached only after a
    # matching token comparison.
    assert body.index("Write-Output 'RECYCLED'") < body.index("Stop-Tree ([int]$p)")

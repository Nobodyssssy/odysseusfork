"""A user Stop must be durable, idempotent and must never be resurrected.

The client used to keep a Stop in browser storage only, so a page refresh, an
Odysseus restart or a second device never learned the task was cancelled. The
cancellation is now recorded in the on-disk cookbook state (the cross-device
source of truth) and the status poller honours it. ``cancel_requested`` is a
request; ``stopped`` is only claimed after liveness was verified false.
"""
import json
import subprocess
import sys
import time

import httpx
import pytest
from fastapi import FastAPI

import routes.cookbook_routes as routes
from src import process_ownership
from core.middleware import INTERNAL_TOOL_HEADER, INTERNAL_TOOL_TOKEN

_INVALID_SID = "x'; echo escaped ; #"


def _state_file(tmp_path):
    return tmp_path / "cookbook_state.json"


def _write_state(path, tasks):
    path.write_text(json.dumps({"tasks": tasks, "env": {"servers": []}}), encoding="utf-8")


def _task(sid="serve-cancel1", status="running", **extra):
    task = {
        "sessionId": sid,
        "type": "download",
        "status": status,
        "modelId": "org/model",
        "remoteHost": "",
        "platform": "windows",
        "output": "",
        "payload": {"repo_id": "org/model"},
    }
    task.update(extra)
    return task


def _app(monkeypatch, tmp_path):
    monkeypatch.setattr(routes, "_COOKBOOK_STATE_PATH", _state_file(tmp_path))
    monkeypatch.setattr(routes, "TMUX_LOG_DIR", tmp_path)
    app = FastAPI()
    app.include_router(routes.setup_cookbook_routes())
    return app


def _client(app):
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test",
                             headers={INTERNAL_TOOL_HEADER: INTERNAL_TOOL_TOKEN})


# -- recording ---------------------------------------------------------------

@pytest.mark.asyncio
async def test_cancel_rejects_an_invalid_session_id(monkeypatch, tmp_path):
    app = _app(monkeypatch, tmp_path)
    async with _client(app) as client:
        response = await client.post("/api/cookbook/tasks/cancel", json={"session_id": _INVALID_SID})
    assert response.status_code == 400
    assert not _state_file(tmp_path).exists()


@pytest.mark.asyncio
async def test_cancel_unknown_task_is_404_and_writes_nothing(monkeypatch, tmp_path):
    app = _app(monkeypatch, tmp_path)
    _write_state(_state_file(tmp_path), [_task(sid="serve-other")])
    before = _state_file(tmp_path).read_text(encoding="utf-8")
    async with _client(app) as client:
        response = await client.post("/api/cookbook/tasks/cancel", json={"session_id": "serve-missing"})
    assert response.status_code == 404
    assert _state_file(tmp_path).read_text(encoding="utf-8") == before


@pytest.mark.asyncio
async def test_cancel_persists_and_is_idempotent(monkeypatch, tmp_path):
    app = _app(monkeypatch, tmp_path)
    _write_state(_state_file(tmp_path), [_task(status="running", output="keep me")])
    async with _client(app) as client:
        first = await client.post("/api/cookbook/tasks/cancel", json={"session_id": "serve-cancel1"})
        second = await client.post("/api/cookbook/tasks/cancel", json={"session_id": "serve-cancel1"})
    assert first.status_code == second.status_code == 200
    assert first.json()["changed"] is True
    assert second.json()["changed"] is False          # idempotent
    stored = json.loads(_state_file(tmp_path).read_text(encoding="utf-8"))["tasks"][0]
    assert stored["status"] == "cancel_requested"
    assert stored["_cancelRequestedAt"] > 0
    assert stored["output"] == "keep me"              # history preserved


@pytest.mark.asyncio
async def test_cancel_keeps_the_admin_gate(monkeypatch, tmp_path):
    """The new mutating endpoint must not be reachable anonymously."""
    monkeypatch.setenv("AUTH_ENABLED", "true")
    monkeypatch.setattr(routes, "_COOKBOOK_STATE_PATH", _state_file(tmp_path))
    monkeypatch.setattr(routes, "TMUX_LOG_DIR", tmp_path)
    _write_state(_state_file(tmp_path), [_task()])
    app = FastAPI()
    app.include_router(routes.setup_cookbook_routes())
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/api/cookbook/tasks/cancel", json={"session_id": "serve-cancel1"})
    assert response.status_code == 403
    # And an invalid id is still rejected before any state is touched.
    assert json.loads(_state_file(tmp_path).read_text(encoding="utf-8"))["tasks"][0]["status"] == "running"


@pytest.mark.asyncio
async def test_cancel_survives_a_reloaded_state(monkeypatch, tmp_path):
    """The state file is the record: a fresh router sees the cancellation."""
    app = _app(monkeypatch, tmp_path)
    _write_state(_state_file(tmp_path), [_task(status="running")])
    async with _client(app) as client:
        await client.post("/api/cookbook/tasks/cancel", json={"session_id": "serve-cancel1"})
    # A brand-new app instance, as after an Odysseus restart.
    app2 = _app(monkeypatch, tmp_path)
    async with _client(app2) as client:
        state = await client.get("/api/cookbook/state")
    assert state.json()["tasks"][0]["status"] == "cancel_requested"


# -- poller honours it -------------------------------------------------------

@pytest.mark.asyncio
async def test_poller_reports_stopping_while_the_process_is_still_alive(monkeypatch, tmp_path):
    proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    try:
        app = _app(monkeypatch, tmp_path)
        _write_state(_state_file(tmp_path), [_task(status="cancel_requested", _cancelRequestedAt=time.time())])
        (tmp_path / "serve-cancel1.pid").write_text(str(proc.pid), encoding="utf-8")
        (tmp_path / "serve-cancel1.pid.token").write_text(
            process_ownership.start_token(proc.pid) or "win32:1", encoding="utf-8")
        async with _client(app) as client:
            data = (await client.get("/api/cookbook/tasks/status")).json()
        row = next(t for t in data["tasks"] if t["session_id"] == "serve-cancel1")
        assert row["status"] == "stopping"            # honest: not verified dead yet
    finally:
        proc.kill()
        proc.wait(timeout=10)


@pytest.mark.asyncio
async def test_poller_promotes_to_stopped_only_after_verified_death(monkeypatch, tmp_path):
    proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    app = _app(monkeypatch, tmp_path)
    _write_state(_state_file(tmp_path), [_task(status="cancel_requested", _cancelRequestedAt=time.time())])
    (tmp_path / "serve-cancel1.pid").write_text(str(proc.pid), encoding="utf-8")
    (tmp_path / "serve-cancel1.pid.token").write_text(
        process_ownership.start_token(proc.pid) or "win32:1", encoding="utf-8")
    proc.kill()
    proc.wait(timeout=10)
    async with _client(app) as client:
        data = (await client.get("/api/cookbook/tasks/status")).json()
    row = next(t for t in data["tasks"] if t["session_id"] == "serve-cancel1")
    assert row["status"] == "stopped"
    # The promotion is durable, not just reported.
    assert json.loads(_state_file(tmp_path).read_text(encoding="utf-8"))["tasks"][0]["status"] == "stopped"


@pytest.mark.asyncio
async def test_poller_never_reports_a_cancelled_task_as_running(monkeypatch, tmp_path):
    """A recycled pid must not resurrect a cancelled task."""
    proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    try:
        app = _app(monkeypatch, tmp_path)
        _write_state(_state_file(tmp_path), [_task(status="cancel_requested", _cancelRequestedAt=time.time())])
        # A pid carrying somebody else's identity: liveness alone says "alive".
        (tmp_path / "serve-cancel1.pid").write_text(str(proc.pid), encoding="utf-8")
        (tmp_path / "serve-cancel1.pid.token").write_text("win32:1", encoding="utf-8")
        async with _client(app) as client:
            data = (await client.get("/api/cookbook/tasks/status")).json()
        row = next(t for t in data["tasks"] if t["session_id"] == "serve-cancel1")
        assert row["status"] in {"stopping", "stopped"}
        assert row["status"] != "running"
    finally:
        proc.kill()
        proc.wait(timeout=10)

"""The browser must record a Stop on the server, not only in localStorage.

A Stop used to live in browser storage, so a page refresh, an Odysseus restart
or a second device never learned the task was cancelled. The stop and kill
handlers now POST /api/cookbook/tasks/cancel first, and the self-heal loop must
never flip a deliberately cancelled task back to running.
"""
from pathlib import Path


SOURCE = Path(__file__).resolve().parents[1] / "static" / "js" / "cookbookRunning.js"


def _source() -> str:
    return SOURCE.read_text(encoding="utf-8")


def test_cancel_request_helper_targets_the_new_endpoint():
    source = _source()
    assert "async function _requestTaskCancel(task)" in source
    assert "'/api/cookbook/tasks/cancel'" in source
    assert "body: JSON.stringify({ session_id: sid })" in source
    # Best-effort: a failed write must not abort the kill that follows.
    start = source.index("async function _requestTaskCancel")
    end = source.index("\nfunction ", start)
    assert "try {" in source[start:end] and "} catch {}" in source[start:end]


def test_cancel_helper_refuses_an_unsafe_session_id():
    source = _source()
    start = source.index("async function _requestTaskCancel")
    end = source.index("\nfunction ", start)
    body = source[start:end]
    assert "const sid = _safeSessionId(task);" in body
    assert "if (!sid) return;" in body


def test_stop_and_kill_handlers_record_the_cancellation():
    source = _source()
    assert source.count("await _requestTaskCancel(task);") >= 2
    # The stop handler records it before signalling anything.
    stop = source.index(".cookbook-task-action-stop")
    kill = source.index(".cookbook-task-action-kill")
    assert source.index("await _requestTaskCancel(task);", stop) < source.index(
        "_tmuxGracefulKill(task)", stop)
    assert "await _requestTaskCancel(task);" in source[kill:kill + 600]


def test_self_heal_never_resurrects_a_cancelled_task():
    source = _source()
    start = source.index("async function _selfHealStaleTasksImpl")
    end = source.index("\nfunction ", start)
    body = source[start:end]
    assert "if (t._cancelRequestedAt) return false;" in body

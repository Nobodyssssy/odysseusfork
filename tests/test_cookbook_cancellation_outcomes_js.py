"""A Stop must not claim success it did not achieve.

Three cases the first cancellation pass got wrong:

  * the cancellation POST failed — the row still vanished and the task looked
    stopped, so the poller (or another device) would bring it back;
  * the kill command failed — the stop handler ignored the outcome entirely;
  * a legacy task with no recorded identity — the server prints UNVERIFIED and
    refuses to signal, but the old handler dropped the row with no explanation.

The handler must record first, check the kill, and restore a visible running
state with an explanation when either step fails.
"""
from pathlib import Path


SOURCE = Path(__file__).resolve().parents[1] / "static" / "js" / "cookbookRunning.js"


def _source() -> str:
    return SOURCE.read_text(encoding="utf-8")


def _between(source: str, start_marker: str, end_marker: str) -> str:
    start = source.index(start_marker)
    end = source.index(end_marker, start)
    return source[start:end]


def _stop_handler(source: str) -> str:
    # The kill button's HTML appears before the stop listener, so anchor on the
    # listeners themselves.
    return _between(source, ".cookbook-task-action-stop').addEventListener",
                    ".cookbook-task-action-kill').addEventListener")


def test_cancel_request_reports_failure_instead_of_swallowing_it():
    source = _source()
    body = _between(source, "async function _requestTaskCancel", "function _restoreRunningBadge")
    assert "if (!r.ok) return false;" in body
    assert "return !!(body && body.ok);" in body
    assert "catch { return false; }" in body


def test_stop_aborts_when_the_cancellation_could_not_be_recorded():
    handler = _stop_handler(_source())
    assert "const recorded = await _requestTaskCancel(task);" in handler
    assert "if (!recorded) {" in handler
    assert "Stop could not be recorded on the server" in handler
    # The row must not be removed on that path.
    assert handler.index("if (!recorded) {") < handler.index("_animateOutThenRemove(el, task.sessionId);")
    assert "_restoreRunningBadge(el, badge, task);" in handler
    assert "_updateTask(task.sessionId, { _userStopped: false });" in handler


def test_stop_checks_the_kill_outcome_before_removing_the_row():
    handler = _stop_handler(_source())
    assert "let stopOk = true;" in handler
    assert "const out = await r.json().catch(() => null);" in handler
    assert "if (out && (out.exit_code || 0) !== 0) stopOk = false;" in handler
    assert "if (!stopOk) {" in handler
    assert "Stop failed — the task may still be running." in handler
    assert handler.index("if (!stopOk) {") < handler.index("_animateOutThenRemove(el, task.sessionId);")


def test_stop_explains_a_refused_unverified_or_recycled_pid():
    handler = _stop_handler(_source())
    assert "if (/UNVERIFIED/i.test(text)) stopNote = 'unverified';" in handler
    assert "else if (/RECYCLED/i.test(text)) stopNote = 'recycled';" in handler
    assert "if (stopNote) {" in handler
    assert "this task has no recorded process identity" in handler
    assert "the recorded pid now belongs to a different process" in handler
    assert handler.index("if (stopNote) {") < handler.index("_animateOutThenRemove(el, task.sessionId);")


def test_force_kill_requires_explicit_authorisation_when_the_record_fails():
    """Force stop is the emergency path; it must not kill unrecorded."""
    source = _source()
    handler = _between(source, ".cookbook-task-action-kill').addEventListener",
                        ".cookbook-task-action-retry')")
    assert "const recorded = await _requestTaskCancel(task);" in handler
    gate = handler.index("if (!recorded) {")
    confirm = handler.index("uiModule.styledConfirm(", gate)
    abort = handler.index("if (!authorised) {", confirm)
    kill = handler.index("_tmuxGracefulKill(task)", abort)
    # The kill is reached only after an explicit confirmation.
    assert "confirmText: 'Force stop'" in handler[confirm:abort]
    assert "return;" in handler[abort:kill]
    # And it still says so afterwards rather than implying a recorded cancel.
    assert "the cancellation could not be recorded on the server" in handler


def test_restore_helper_puts_the_row_back_to_running():
    source = _source()
    assert "function _restoreRunningBadge(el, badge, task)" in source
    start = source.index("function _restoreRunningBadge")
    end = source.index("\n}", start)
    body = source[start:end]
    assert "el.dataset.status = 'running';" in body
    assert "_statusLabel('running', task.type)" in body


def test_failed_tasks_auto_expand_without_a_click():
    """A failed serve must show its reason immediately.

    Regression: only downloads auto-expanded, so a serve that died with exit
    127 sat collapsed behind a neutral badge until the user double-clicked it.
    """
    source = SOURCE.read_text(encoding="utf-8")

    assert "if (['error', 'crashed', 'failed'].includes(status)) return true;" in source

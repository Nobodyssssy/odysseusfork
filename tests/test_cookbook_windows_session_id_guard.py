"""A session id must never reach a shell/PowerShell command unvalidated.

The Windows stop path interpolates ``task.sessionId`` into a PowerShell command
that ends with ``Remove-Item -Force``. A crafted id (from a synced cookbook
state file) closes the single-quoted literal and runs arbitrary PowerShell —
verified against a real ``powershell -Command`` with a benign payload. The
backend mints ids as cookbook-<hex>/serve-<hex>/queue-<hex> and validates them
with _SESSION_ID_RE; the command builders must apply the same shape.
"""
from pathlib import Path


SOURCE = Path(__file__).resolve().parents[1] / "static" / "js" / "cookbookRunning.js"


def _source() -> str:
    return SOURCE.read_text(encoding="utf-8")


def test_session_id_guard_matches_the_backend_shape():
    source = _source()
    # Mirrors routes/cookbook_helpers.py::_SESSION_ID_RE
    assert "const _SESSION_ID_RE = /^[A-Za-z0-9_-]{1,64}$/;" in source
    assert "function _safeSessionId(task)" in source
    # Refuse, don't escape: an id outside the shape yields no command.
    assert "return _SESSION_ID_RE.test(sid) ? sid : '';" in source


def test_every_command_builder_obtains_the_id_through_the_guard():
    source = _source()
    builders = (
        "function _winSessionCmd(task, tmuxArgs)",
        "function _winSessionStopTreePs(task)",
        "export function _tmuxForceKill(task)",
        "export function _tmuxIsAliveCheck(task)",
        "export function _tmuxGracefulKill(task)",
    )
    for builder in builders:
        start = source.index(builder)
        # The next function boundary delimits the body.
        end = source.find("\nfunction ", start + len(builder))
        end2 = source.find("\nexport function ", start + len(builder))
        ends = [e for e in (end, end2) if e != -1]
        body = source[start:min(ends) if ends else len(source)]
        assert "_safeSessionId(task)" in body, f"{builder} builds a command without the guard"
        assert "if (!sid) return '';" in body, f"{builder} does not fail closed on an unsafe id"


def test_no_command_string_interpolates_the_raw_session_id():
    """`${task.sessionId}` may only appear where it is not a command.

    Allowed: DOM selectors and JS state keys. Refused: anything handed to a
    shell/PowerShell builder.
    """
    import re

    source = _source()
    for match in re.finditer(r"\$\{task\.sessionId\}", source):
        line = source[:match.start()].count(chr(10)) + 1
        text = source.splitlines()[line - 1]
        allowed = ("querySelector" in text or "data-task-id" in text
                   or "_updateTask(" in text or "_removeTask(" in text
                   or "existingIds" in text or "el.dataset" in text
                   or "_animateOutThenRemove" in text)
        assert allowed, f"line {line} interpolates the raw id into a command: {text.strip()}"


def test_guard_rejects_the_verified_escape_payload():
    """The exact payload that escaped in live PowerShell must now be refused."""
    import re

    payload = "x') ; Set-Content -Path 'C:\marker.txt' -Value escaped ; #"
    assert not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", payload)
    for benign in ("cookbook-abc12345", "serve-deadbeef", "queue-1a2b3c4d_x-y"):
        assert re.fullmatch(r"[A-Za-z0-9_-]{1,64}", benign)


def test_guard_is_exported_so_other_modules_reuse_one_definition():
    source = _source()
    assert "export function _safeSessionId(task)" in source


def test_download_module_guards_its_zombie_probe():
    """cookbookDownload.js probes a tmux session for a zombie download task.

    That id also comes from task data and is interpolated into an
    ssh single-quoted command, so it needs the same shape check.
    """
    source = (Path(__file__).resolve().parents[1] / "static" / "js" / "cookbookDownload.js").read_text(encoding="utf-8")
    assert "import { _safeSessionId } from './cookbookRunning.js';" in source
    assert "const _probeSid = _safeSessionId(zombieCandidate);" in source
    assert "tmux has-session -t ${_probeSid}" in source
    assert "tmux has-session -t ${zombieCandidate.sessionId}" not in source


def test_dom_derived_task_id_is_guarded_before_reaching_a_command():
    """The stop handlers read the id back off the DOM element.

    It round-trips from task data, but it is still data by the time it is
    interpolated, so the command must go through the guard.
    """
    source = _source()
    assert "_tmuxCmd(task, `kill-session -t ${taskId}`)" not in source
    assert "_tmuxCmd(task, `kill-session -t ${_safeSessionId(task)}`)" in source


def test_loop_variable_session_ids_are_guarded():
    source = _source()
    assert "_tmuxCmd(t, `has-session -t ${t.sessionId}`)" not in source
    assert "_tmuxCmd(t, `has-session -t ${_safeSessionId(t)}`)" in source


def test_stop_path_verifies_the_recorded_process_identity():
    """A recycled pid must not be signalled.

    The stop command reads <session>.pid.token (written at launch by
    routes/cookbook_routes.py) and compares the live process start time against
    it. Verified live on this host: matching token -> stops, foreign token ->
    does not stop, missing token -> legacy behaviour (stops).
    """
    source = _source()
    assert "function _winPidStopTreePs(sid, dirExpr)" in source
    assert ".pid.token" in source
    assert "[datetime]::FromFileTimeUtc([uint64]$matches[1])" in source
    assert "$proc.StartTime.ToUniversalTime() -ne" in source
    # The guard runs BEFORE the tree kill, and a mismatch exits without signalling.
    guard = source.index("[datetime]::FromFileTimeUtc([uint64]$matches[1])")
    kill = source.index("Stop-Tree ([int]$p) }; ", guard)
    assert "exit 0 }" in source[guard:kill]

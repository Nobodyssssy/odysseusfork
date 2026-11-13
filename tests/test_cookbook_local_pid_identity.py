"""A detached local-Windows task must not be reported running on a recycled pid.

The status poller used a bare `pid_alive` on the recorded <session>.pid. A pid
is not an identity: once the real process exits, Windows can hand that number
to an unrelated process, which then reads as "alive" forever — the task is
falsely running, and a stop would signal a bystander. The launch path now
records the process creation time (process_ownership.start_token) and the
poller verifies it before reporting liveness.
"""
import subprocess
import sys
import time

import pytest

import routes.cookbook_routes as routes


@pytest.fixture()
def log_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(routes, "TMUX_LOG_DIR", tmp_path)
    return tmp_path


def _spawn(duration=30):
    return subprocess.Popen([sys.executable, "-c", f"import time; time.sleep({duration})"])


def test_recorded_identity_matches_while_the_process_lives(log_dir):
    proc = _spawn()
    try:
        routes._record_local_pid_identity("cookture-fix", proc.pid)
        assert (log_dir / "cookture-fix.pid.token").exists()
        assert routes._local_pid_is_owned(proc.pid, "cookture-fix") is True
    finally:
        proc.kill()
        proc.wait(timeout=10)


def test_exited_process_is_not_reported_running(log_dir):
    proc = _spawn(duration=1)
    routes._record_local_pid_identity("cookture-gone", proc.pid)
    proc.wait(timeout=10)
    assert routes.pid_alive(proc.pid) is False
    assert routes._local_pid_is_owned(proc.pid, "cookture-gone") is False


def test_recycled_pid_is_not_accepted_as_the_launched_process(log_dir):
    """A live pid carrying another process's token must read as not-ours."""
    other = _spawn()
    victim = _spawn()
    try:
        # Record the identity of `other`, then present it as `victim`'s.
        routes._record_local_pid_identity("cookture-reuse", other.pid)
        token = (log_dir / "cookture-reuse.pid.token").read_text(encoding="utf-8")
        (log_dir / "cookture-victim.pid.token").write_text(token, encoding="utf-8")

        assert routes.pid_alive(victim.pid) is True          # bare probe says alive
        assert routes._local_pid_is_owned(victim.pid, "cookture-victim") is False
    finally:
        for proc in (other, victim):
            proc.kill()
            proc.wait(timeout=10)


def test_task_without_a_recorded_identity_keeps_the_bare_probe(log_dir):
    """Tasks launched before the identity existed must not disappear."""
    proc = _spawn()
    try:
        assert routes._local_pid_is_owned(proc.pid, "cookture-legacy") is True
    finally:
        proc.kill()
        proc.wait(timeout=10)


def test_record_identity_never_raises(log_dir):
    """Best-effort: a pid this host cannot inspect must not break the launch."""
    proc = _spawn(duration=1)
    proc.wait(timeout=10)
    # A just-exited process, an implausible pid and a non-numeric value.
    routes._record_local_pid_identity("cookture-dead", proc.pid)
    routes._record_local_pid_identity("cookture-bogus", 999999999)
    routes._record_local_pid_identity("cookture-none", None)

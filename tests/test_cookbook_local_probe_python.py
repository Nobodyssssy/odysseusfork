"""The local cache probes must use the running interpreter, not a bare python3.

A native Windows virtual environment ships ``python.exe`` and no
``python3.exe``. The probes in the Cookbook status path hardcoded ``python3``,
so on such a host they either fail (no such executable) or silently run under
whatever unrelated interpreter happens to be first on PATH — the probes then
classify downloads from the wrong environment. They must use the interpreter
Odysseus itself runs under, matching how /api/model/cached already resolves it.
"""
import os
import sys

from routes.cookbook_routes import _local_probe_python


def test_local_probe_python_prefers_the_running_interpreter():
    assert _local_probe_python() == sys.executable


def test_local_probe_python_never_returns_a_bare_name(monkeypatch):
    """With no running interpreter it must resolve an executable, not "python3"."""
    import routes.cookbook_routes as routes

    monkeypatch.setattr(routes.sys, "executable", "")
    monkeypatch.setattr(routes, "which_tool", lambda name: None)
    assert _local_probe_python() == "python"


def test_local_probe_python_falls_back_to_a_resolved_executable(monkeypatch):
    import routes.cookbook_routes as routes

    monkeypatch.setattr(routes.sys, "executable", "")
    monkeypatch.setattr(routes, "which_tool", lambda name: f"C:\tools\{name}.exe")
    assert _local_probe_python() == "C:\tools\python.exe"


def test_generated_probe_command_uses_the_resolved_interpreter():
    """The status-path probes build their argv through the resolver."""
    import inspect

    import routes.cookbook_routes as routes

    source = inspect.getsource(routes.setup_cookbook_routes)
    assert '_local_probe_python(), "-c", HF_CACHE_COMPLETE_PROBE' in source
    assert '_local_probe_python(), "-c", HF_CACHE_INCOMPLETE_PROBE' in source
    assert '["python3", "-c", HF_CACHE_' not in source

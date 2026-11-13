"""Native Windows llama.cpp runtime management.

These tests use controlled local archives and fake executables — nothing is
downloaded and no real runtime is installed.
"""
import os
import stat
import sys
import zipfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src import llama_runtime as lr  # noqa: E402


def _fake_server(path: Path, body: str = 'echo "llama-server (fake) b1"\n') -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("#!/usr/bin/env bash\n" + body, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IEXEC)
    return path


def _zip_with_server(dest: Path, member: str = "llama-b1-bin-win-cuda-x64/llama-server.exe") -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(dest, "w") as zf:
        zf.writestr(member, "#!/usr/bin/env bash\necho 'llama-server (fake) b1'\n")
    return dest


def test_asset_selection_matches_variant_and_skips_arm():
    release = {"assets": [
        {"name": "llama-b1-bin-win-cuda-cu12.4-x64.zip", "browser_download_url": "https://x/a"},
        {"name": "llama-b1-bin-win-cpu-x64.zip", "browser_download_url": "https://x/b"},
        {"name": "llama-b1-bin-win-vulkan-x64.zip", "browser_download_url": "https://x/c"},
        {"name": "llama-b1-bin-win-arm64.zip", "browser_download_url": "https://x/d"},
        {"name": "llama-b1-bin-linux-x64.tar.gz", "browser_download_url": "https://x/e"},
    ]}
    assert lr.select_llama_asset(release, "cuda")["name"].endswith("win-cuda-cu12.4-x64.zip")
    assert "cpu" in lr.select_llama_asset(release, "cpu")["name"]
    assert "vulkan" in lr.select_llama_asset(release, "vulkan")["name"]
    # No Windows asset at all -> no guess.
    assert lr.select_llama_asset({"assets": release["assets"][4:]}, "cuda") is None


def test_variants_are_platform_appropriate():
    win = lr.platform_llama_variants(is_windows=True, is_wsl=False, is_macos=False,
                                     is_apple_silicon=False, has_nvidia=True)
    assert [v["id"] for v in win] == ["cuda", "vulkan", "cpu", "sycl"]
    assert win[0]["recommended"] is True and win[0]["prebuilt"] is True

    win_cpu = lr.platform_llama_variants(is_windows=True, is_wsl=False, is_macos=False,
                                         is_apple_silicon=False, has_nvidia=False)
    assert [v["id"] for v in win_cpu if v["recommended"]] == ["cpu"]

    linux = lr.platform_llama_variants(is_windows=False, is_wsl=False, is_macos=False,
                                       is_apple_silicon=False, has_nvidia=True)
    assert all(not v["prebuilt"] for v in linux)

    mac = lr.platform_llama_variants(is_windows=False, is_wsl=False, is_macos=True,
                                     is_apple_silicon=True, has_nvidia=False)
    assert mac == [{"id": "source", "label": "Build from source (CMake)",
                    "requires": "Xcode command line tools.", "recommended": True,
                    "prebuilt": False}]


def test_extract_rejects_path_traversal(tmp_path):
    bad = tmp_path / "bad.zip"
    with zipfile.ZipFile(bad, "w") as zf:
        zf.writestr("../escape/llama-server.exe", "x")
    ok, message, exe = lr.extract_llama_archive(bad, tmp_path / "out")
    assert ok is False and "unsafe" in message.lower()
    assert exe is None


def test_install_is_atomic_and_keeps_previous(tmp_path, monkeypatch):
    monkeypatch.setattr(lr, "llama_runtime_root", lambda: tmp_path / "runtimes")
    first = _zip_with_server(tmp_path / "first.zip")
    ok, _msg, info = lr.install_llama_archive(first, "b1")
    assert ok and info["executable"]
    target = lr.llama_install_dir("b1")
    assert target.is_dir() and not (tmp_path / "runtimes" / "b1.staging").exists()

    second = _zip_with_server(tmp_path / "second.zip")
    ok, _msg, info2 = lr.install_llama_archive(second, "b1")
    assert ok and info2["previous"]
    assert lr.llama_previous_dir("b1").is_dir()

    ok, restored = lr.roll_back_llama_runtime("b1")
    assert ok and Path(restored).is_file()


def test_install_rejects_archive_without_server(tmp_path, monkeypatch):
    monkeypatch.setattr(lr, "llama_runtime_root", lambda: tmp_path / "runtimes")
    empty = tmp_path / "empty.zip"
    with zipfile.ZipFile(empty, "w") as zf:
        zf.writestr("readme.txt", "nothing here")
    ok, message, info = lr.install_llama_archive(empty, "b2")
    assert ok is False and "does not contain" in message


def test_validate_runtime_exe_uses_argument_array(tmp_path):
    exe = _fake_server(tmp_path / "llama-server.exe")
    ok, detail = lr.validate_runtime_exe(exe)
    assert ok and "llama-server" in detail

    # A path with spaces and quotes must still be a single argument.
    spaced = _fake_server(tmp_path / "dir with space" / "llama-server.exe")
    ok2, _ = lr.validate_runtime_exe(spaced)
    assert ok2


def test_llama_runtime_root_is_application_managed():
    root = lr.llama_runtime_root()
    assert root.name == "llama.cpp"
    assert "runtimes" in root.parts
    # Never a global location.
    assert Path(os.path.expanduser("~")) not in root.parents or "runtimes" in root.parts


@pytest.mark.asyncio
async def test_packages_row_does_not_call_a_wheel_a_runtime(monkeypatch):
    """"llama_cpp installed" must mean a runnable llama-server, not a pip wheel.

    On this host `shutil.which("llama-server")` returns a stale wrapper whose
    exec target was deleted, so the row used to read "installed" with no
    verification at all.
    """
    from unittest.mock import MagicMock
    from types import SimpleNamespace
    import routes.shell_routes as shell_routes
    from starlette.requests import Request

    monkeypatch.setattr(shell_routes, "_require_admin", lambda request: None)
    monkeypatch.setattr(shell_routes, "_reject_cross_site", lambda request: None)
    monkeypatch.setattr(shell_routes, "_PACKAGE_STATUS_CACHE", {})
    monkeypatch.setattr(shell_routes.shutil, "which", lambda name: None)
    monkeypatch.setattr(shell_routes, "IS_APPLE_SILICON", False)
    import importlib.metadata as md
    monkeypatch.setattr(md, "version", lambda name: "0.1.0")

    router = shell_routes.setup_shell_routes()
    endpoint = next(
        r.endpoint for r in router.routes
        if getattr(r, "path", None) == "/api/cookbook/packages" and "GET" in r.methods
    )
    http_req = Request({
        "type": "http", "method": "GET", "path": "/api/cookbook/packages", "headers": [],
        "app": SimpleNamespace(state=SimpleNamespace(auth_manager=SimpleNamespace(is_configured=True))),
    })
    http_req.state.current_user = "admin"
    data = await endpoint(http_req, None, None, None, None, None, None)
    row = next(p for p in data["packages"] if p["name"] == "llama_cpp")
    assert row["installed"] is False
    assert row["partial"] is True
    assert "Python package only" in row["status_note"]
    assert row["system_prereqs"] == []
    assert row["prebuilt"] is True


@pytest.mark.asyncio
async def test_packages_marks_tmux_not_applicable_on_native_windows(monkeypatch):
    from types import SimpleNamespace
    import routes.shell_routes as shell_routes
    from starlette.requests import Request

    monkeypatch.setattr(shell_routes, "_require_admin", lambda request: None)
    monkeypatch.setattr(shell_routes, "_reject_cross_site", lambda request: None)
    monkeypatch.setattr(shell_routes, "_PACKAGE_STATUS_CACHE", {})
    monkeypatch.setattr(shell_routes.shutil, "which", lambda name: None)
    monkeypatch.setattr(shell_routes, "IS_APPLE_SILICON", False)

    router = shell_routes.setup_shell_routes()
    endpoint = next(
        r.endpoint for r in router.routes
        if getattr(r, "path", None) == "/api/cookbook/packages" and "GET" in r.methods
    )
    http_req = Request({
        "type": "http", "method": "GET", "path": "/api/cookbook/packages", "headers": [],
        "app": SimpleNamespace(state=SimpleNamespace(auth_manager=SimpleNamespace(is_configured=True))),
    })
    http_req.state.current_user = "admin"
    data = await endpoint(http_req, None, None, None, None, None, None)
    tmux = next(p for p in data["packages"] if p["name"] == "tmux")
    assert tmux["applicable"] is False
    assert "Windows" in tmux["status_note"]

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


def test_asset_selection_matches_variant_and_skips_non_server_assets():
    # Names mirror the real ggml-org/llama.cpp nightly assets.
    release = {"assets": [
        {"name": "cudart-llama-bin-win-cuda-12.4-x64.zip", "browser_download_url": "https://x/cudart"},
        {"name": "llama-b12368-bin-win-cuda-12.4-x64.zip", "browser_download_url": "https://x/a"},
        {"name": "llama-b12368-bin-win-cuda-13.5-x64.zip", "browser_download_url": "https://x/a13"},
        {"name": "llama-b12368-bin-win-cpu-x64.zip", "browser_download_url": "https://x/b"},
        {"name": "llama-b12368-bin-win-vulkan-x64.zip", "browser_download_url": "https://x/c"},
        {"name": "llama-b12368-bin-win-sycl-x64.zip", "browser_download_url": "https://x/s"},
        {"name": "llama-b12368-bin-win-rocm-10.0-x64.zip", "browser_download_url": "https://x/r"},
        {"name": "llama-b12368-bin-win-cuda-12.4-arm64.zip", "browser_download_url": "https://x/arm"},
        {"name": "llama-b12368-bin-linux-x64.tar.gz", "browser_download_url": "https://x/e"},
    ]}
    # CUDA 12.4 is preferred over CUDA 13.x, and cudart-/rocm/arm are not servers.
    cuda = lr.select_llama_asset(release, "cuda", "32.0.16.2101")
    assert cuda["name"] == "llama-b12368-bin-win-cuda-12.4-x64.zip"
    assert "driver_warning" not in cuda
    assert "cpu" in lr.select_llama_asset(release, "cpu")["name"]
    assert "vulkan" in lr.select_llama_asset(release, "vulkan")["name"]
    assert "sycl" in lr.select_llama_asset(release, "sycl")["name"]
    # A release with no Windows binaries -> no guess.
    assert lr.select_llama_asset({"assets": release["assets"][8:]}, "cuda") is None


def test_cuda_13_warns_when_driver_is_old():
    release = {"assets": [
        {"name": "llama-b1-bin-win-cuda-12.4-x64.zip", "browser_download_url": "u12"},
        {"name": "llama-b1-bin-win-cuda-13.5-x64.zip", "browser_download_url": "u13"},
    ]}
    # With no CUDA 12 asset present, 13.5 is still selected but flagged.
    only13 = {"assets": release["assets"][1:]}
    asset = lr.select_llama_asset(only13, "cuda", "32.0.16.2101")
    assert "13.5" in asset["name"]
    assert "newer NVIDIA driver" in asset["driver_warning"]


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
    # The reported executable must be the activated path, never the staging one.
    assert ".staging" not in info2["executable"]
    assert info2["executable"].startswith(str(target))
    assert info2["version"]

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


# ── installer hardening ────────────────────────────────────────────────

def test_install_dir_cannot_escape_runtime_root():
    # A hostile tag must not be able to point outside the runtime directory.
    weird = lr.llama_install_dir("../../../../Windows/System32")
    assert ".." not in Path(weird).parts
    assert weird.parent.name == "llama.cpp"


def test_symlink_archive_member_is_rejected(tmp_path):
    archive = tmp_path / "link.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        info = zipfile.ZipInfo("llama-bin/llama-server.exe")
        info.external_attr = (stat.S_IFLNK | 0o777) << 16
        zf.writestr(info, "target")
    ok, message, exe = lr.extract_llama_archive(archive, tmp_path / "out")
    assert ok is False and "non-regular" in message


def test_failed_reinstall_keeps_previous_runtime(tmp_path, monkeypatch):
    monkeypatch.setattr(lr, "llama_runtime_root", lambda: tmp_path / "runtimes")
    ok, _m, info = lr.install_llama_archive(_zip_with_server(tmp_path / "good.zip"), "b7")
    assert ok
    good_exe = info["executable"]

    broken = tmp_path / "broken.zip"
    with zipfile.ZipFile(broken, "w") as zf:
        zf.writestr("llama-bin/llama-server.exe", "#!/usr/bin/env bash\nexit 127\n")
    ok2, message, _ = lr.install_llama_archive(broken, "b7")
    assert ok2 is False
    # The activated runtime is untouched, and the failed staging dir is gone.
    assert Path(good_exe).is_file()
    assert not (tmp_path / "runtimes" / "b7.staging").exists()
    assert not (tmp_path / "runtimes" / "b7.previous").exists()


@pytest.mark.asyncio
async def test_install_endpoint_rejects_non_official_and_escaping_sources(monkeypatch, tmp_path):
    from types import SimpleNamespace
    import routes.shell_routes as shell_routes
    from starlette.requests import Request

    monkeypatch.setattr(shell_routes, "_require_admin", lambda request: None)
    monkeypatch.setattr(shell_routes, "_reject_cross_site", lambda request: None)

    router = shell_routes.setup_shell_routes()
    endpoint = next(
        r.endpoint for r in router.routes
        if getattr(r, "path", None) == "/api/cookbook/llama-runtime/install" and "POST" in r.methods
    )

    def _req(payload):
        req = Request({"type": "http", "method": "POST", "path": "/x", "headers": [],
                       "app": SimpleNamespace(state=SimpleNamespace(auth_manager=SimpleNamespace()))})
        async def _json():
            return payload
        req.json = _json
        return req

    import fastapi

    async def _expect_400(payload):
        with pytest.raises(fastapi.HTTPException) as exc:
            await endpoint(_req(payload), payload)
        assert exc.value.status_code == 400

    # Non-official host, plain http, and an archive outside the runtime dir.
    await _expect_400({"variant": "cuda", "tag": "b1", "url": "https://evil.example/x.zip"})
    await _expect_400({"variant": "cuda", "tag": "b1", "url": "http://github.com/x.zip"})
    outside = tmp_path / "outside.zip"
    outside.write_text("nope")
    await _expect_400({"variant": "cuda", "tag": "b1", "archive_path": str(outside)})


@pytest.mark.asyncio
async def test_windows_install_refuses_vllm_and_sglang(monkeypatch):
    from types import SimpleNamespace
    import routes.shell_routes as shell_routes
    from starlette.requests import Request

    monkeypatch.setattr(shell_routes, "_require_admin", lambda request: None)
    router = shell_routes.setup_shell_routes()
    endpoint = next(
        r.endpoint for r in router.routes
        if getattr(r, "path", None) == "/api/cookbook/packages/install" and "POST" in r.methods
    )

    for name in ("vllm", "sglang[all]", "mlx-lm"):
        req = Request({"type": "http", "method": "POST", "path": "/x", "headers": [],
                       "app": SimpleNamespace(state=SimpleNamespace(auth_manager=SimpleNamespace()))})
        async def _json(name=name):
            return {"pip": name}
        req.json = _json
        body = await endpoint(req)
        assert body["ok"] is False
        assert "Windows" in body["error"] or "Apple" in body["error"]


@pytest.mark.asyncio
async def test_windows_catalog_marks_engines_unsupported(monkeypatch):
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
    http_req = Request({"type": "http", "method": "GET", "path": "/api/cookbook/packages", "headers": [],
                       "app": SimpleNamespace(state=SimpleNamespace(auth_manager=SimpleNamespace(is_configured=True)))})
    http_req.state.current_user = "admin"
    data = await endpoint(http_req, None, None, None, None, None, None)
    for name in ("vllm", "sglang"):
        row = next(p for p in data["packages"] if p["name"] == name)
        assert row["applicable"] is False
        assert "no native Windows build" in row["status_note"]

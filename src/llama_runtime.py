"""llama.cpp runtime discovery, selection and prebuilt installation.

Cookbook's Dependencies tab used to treat ``llama-cpp-python`` (a Python wheel)
as "llama.cpp installed". Those are not the same thing: the wheel is an
in-process binding, while Cookbook launches the standalone ``llama-server``
executable. On native Windows the wheel also does not provide that executable at
all, so the old row showed a Linux build toolchain (cmake/g++/git) as the way
forward.

This module owns the native-Windows path:

* which prebuilt variants are appropriate for the detected platform;
* which official release asset matches a variant;
* a versioned, application-managed install directory (never a global PATH edit);
* archive extraction and ``llama-server.exe --version`` validation;
* atomic replacement with the previous runtime kept for rollback.

Nothing here downloads or installs on import; callers require explicit user
approval.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import zipfile
from pathlib import Path

LLAMA_RELEASE_API = "https://api.github.com/repos/ggml-org/llama.cpp/releases/latest"

# Asset name fragments, most specific first. llama.cpp publishes Windows
# builds as `llama-<bld>-bin-win-<backend>-<arch>.zip`.
VARIANT_ASSETS: dict[str, list[str]] = {
    "cuda": ["win-cuda-", "win-cu12", "win-cu13"],
    "vulkan": ["win-vulkan-"],
    "cpu": ["win-cpu-", "win-avx2-", "win-avx-", "win-x64"],
    "sycl": ["win-sycl-"],
}

# Human-facing requirements. Kept factual: a CUDA build needs a driver new
# enough for the bundled CUDA runtime, and Cookbook never assumes a newer CUDA
# is supported just because an asset exists.
VARIANT_INFO: dict[str, dict] = {
    "cuda": {
        "label": "NVIDIA CUDA",
        "requires": "A recent NVIDIA driver (the build bundles its own CUDA runtime).",
        "recommended_for": "nvidia",
    },
    "vulkan": {
        "label": "Vulkan",
        "requires": "A Vulkan-capable GPU driver.",
        "recommended_for": "gpu",
    },
    "cpu": {
        "label": "CPU only",
        "requires": "No GPU required.",
        "recommended_for": "cpu",
    },
    "sycl": {
        "label": "Intel SYCL",
        "requires": "An Intel GPU with SYCL support.",
        "recommended_for": "intel",
    },
}


def llama_runtime_root() -> Path:
    """Application-managed runtime directory (never on the global PATH)."""
    from src.runtime_paths import get_default_data_dir

    return Path(get_default_data_dir()) / "runtimes" / "llama.cpp"


def llama_install_dir(tag: str) -> Path:
    """Versioned install directory for a release tag or build id."""
    safe = re.sub(r"[^A-Za-z0-9._-]+", "-", str(tag or "")).strip("-")
    return llama_runtime_root() / (safe or "current")


def llama_previous_dir(tag: str) -> Path:
    return llama_runtime_root() / (re.sub(r"[^A-Za-z0-9._-]+", "-", str(tag or "")).strip("-") + ".previous")


def llama_runtime_summary() -> dict:
    """What is installed, and what a fresh install would look like."""
    root = llama_runtime_root()
    installed: list[dict] = []
    try:
        if root.is_dir():
            for child in sorted(root.iterdir()):
                if not child.is_dir() or child.name.endswith(".previous"):
                    continue
                exe = _find_server_exe(child)
                installed.append({
                    "tag": child.name,
                    "path": str(child),
                    "executable": str(exe) if exe else None,
                    "previous": llama_previous_dir(child.name).is_dir(),
                })
    except OSError:
        pass
    return {"root": str(root), "installed": installed}


def _find_server_exe(directory: Path) -> Path | None:
    for name in ("llama-server.exe", "llama-server"):
        candidate = directory / name
        if candidate.is_file():
            return candidate
    # Prebuilt zips nest everything under a single top-level folder.
    for name in ("llama-server.exe", "llama-server"):
        matches = sorted(directory.rglob(name))
        if matches:
            return matches[0]
    return None


def platform_llama_variants(*, is_windows: bool, is_wsl: bool, is_macos: bool,
                            is_apple_silicon: bool, has_nvidia: bool) -> list[dict]:
    """Variants appropriate for this platform, recommended first.

    Source builds are intentionally not offered as a prebuilt variant: they need
    a toolchain the user may not have, so they stay an explicit advanced option
    in the UI rather than a default recommendation.
    """
    if is_macos or is_apple_silicon:
        return [{"id": "source", "label": "Build from source (CMake)", "requires": "Xcode command line tools.",
                 "recommended": True, "prebuilt": False}]
    if not is_windows:
        # Linux and WSL keep the existing supported paths (pip wheel or distro
        # package); prebuilt Windows assets are not applicable.
        return [{"id": "distro", "label": "Distro package or pip wheel", "requires": "See Dependencies tab.",
                 "recommended": True, "prebuilt": False}]

    variants: list[dict] = []
    order = ["cuda", "vulkan", "cpu", "sycl"]
    for vid in order:
        info = VARIANT_INFO[vid]
        recommended = (vid == "cuda" and has_nvidia) or (vid == "cpu" and not has_nvidia)
        variants.append({
            "id": vid,
            "label": info["label"],
            "requires": info["requires"],
            "recommended": recommended,
            "prebuilt": True,
        })
    if is_wsl:
        variants = [v for v in variants if v["id"] in ("cpu", "vulkan")]
    return variants


def select_llama_asset(release: dict, variant_id: str) -> dict | None:
    """Pick the Windows x64 asset for ``variant_id`` from a GitHub release.

    Returns the asset dict, or None when the release has no matching Windows
    build. ARM64 and non-Windows assets are rejected rather than guessed at.
    """
    if variant_id == "source":
        return None
    fragments = VARIANT_ASSETS.get(variant_id)
    if not fragments:
        return None
    assets = release.get("assets") if isinstance(release, dict) else None
    if not isinstance(assets, list):
        return None
    windows = [
        a for a in assets
        if isinstance(a, dict)
        and isinstance(a.get("name"), str)
        and "win" in a["name"].lower()
        and "arm" not in a["name"].lower()
        and a["name"].lower().endswith(".zip")
    ]
    if not windows:
        return None
    for frag in fragments:
        for asset in windows:
            name = asset["name"].lower()
            if frag in name:
                return {
                    "name": asset["name"],
                    "url": asset.get("browser_download_url") or asset.get("url"),
                    "size": asset.get("size"),
                    "id": asset.get("id"),
                }
    return None


def _validate_via_bash(exe: Path) -> tuple[bool, str]:
    """Verify a Windows shell wrapper the way the runner launches it.

    The path is passed as ``$1`` to a fixed script string — never concatenated
    into the shell command, so spaces or quotes in the path cannot change what
    runs.
    """
    try:
        from core.platform_compat import find_bash
        bash = find_bash() or "bash"
    except Exception:
        bash = "bash"
    try:
        proc = subprocess.run(
            [bash, "-c", 'exec "$1" --version', "_", str(exe)],
            capture_output=True, text=True, timeout=60,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return False, f"could not be run through bash: {exc}"
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip().splitlines()
        return False, (detail[0] if detail else f"exit {proc.returncode}")
    text = (proc.stdout or "").strip()
    return True, text.splitlines()[0] if text else ""


def validate_runtime_exe(exe: Path) -> tuple[bool, str]:
    """Run ``exe --version``. Direct argument array, never a shell string."""
    try:
        proc = subprocess.run([str(exe), "--version"], capture_output=True, text=True, timeout=60)
    except OSError as exc:
        # A shell wrapper is not a valid Win32 image, so Windows refuses to
        # execute it directly. The runner launches through Git Bash, so verify
        # it the same way instead of rejecting a wrapper that actually works.
        if os.name == "nt" and getattr(exc, "winerror", None) in (193, 216):
            return _validate_via_bash(exe)
        return False, f"could not be run: {exc}"
    except subprocess.SubprocessError as exc:
        return False, f"could not be run: {exc}"
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip().splitlines()
        return False, (detail[0] if detail else f"exit {proc.returncode}")
    text = (proc.stdout or "").strip()
    return True, text.splitlines()[0] if text else ""


def extract_llama_archive(archive: Path, dest: Path) -> tuple[bool, str, Path | None]:
    """Extract a prebuilt ZIP and return (ok, message, executable)."""
    if not archive.is_file():
        return False, "The download is missing.", None
    try:
        with zipfile.ZipFile(archive) as zf:
            # Refuse path traversal and absolute members before writing anything.
            for member in zf.namelist():
                if member.startswith(("/", "\\")) or ".." in Path(member).parts:
                    return False, f"Refusing unsafe archive member: {member}", None
            zf.extractall(dest)
    except (zipfile.BadZipFile, OSError) as exc:
        return False, f"The archive could not be extracted: {exc}", None
    exe = _find_server_exe(dest)
    if exe is None:
        return False, "The archive does not contain a llama-server executable.", None
    ok, detail = validate_runtime_exe(exe)
    if not ok:
        return False, f"The extracted executable is not usable: {detail}", None
    return True, detail, exe


def install_llama_archive(archive: Path, tag: str) -> tuple[bool, str, dict | None]:
    """Install a downloaded archive atomically, keeping the previous runtime.

    The archive is extracted to a staging directory first; only a fully
    validated extract replaces the current install, and the old one is moved
    aside rather than deleted so a bad update can be rolled back.
    """
    root = llama_runtime_root()
    try:
        root.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        return False, f"Could not create {root}: {exc}", None

    target = llama_install_dir(tag)
    staging = root / (target.name + ".staging")
    previous = llama_previous_dir(tag)
    shutil.rmtree(staging, ignore_errors=True)
    try:
        staging.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        return False, f"Could not create staging dir: {exc}", None

    ok, message, exe = extract_llama_archive(archive, staging)
    if not ok:
        shutil.rmtree(staging, ignore_errors=True)
        return False, message, None

    shutil.rmtree(previous, ignore_errors=True)
    if target.exists():
        try:
            target.rename(previous)
        except OSError as exc:
            shutil.rmtree(staging, ignore_errors=True)
            return False, f"Could not set aside the previous runtime: {exc}", None
    try:
        staging.rename(target)
    except OSError as exc:
        # Put the old runtime back before reporting failure.
        if previous.exists() and not target.exists():
            previous.rename(target)
        shutil.rmtree(staging, ignore_errors=True)
        return False, f"Could not activate the new runtime: {exc}", None

    ok, detail = validate_runtime_exe(exe) if exe else (False, "missing executable")
    info = {
        "tag": target.name,
        "path": str(target),
        "executable": str(exe) if exe else None,
        "version": detail if ok else "",
        "previous": str(previous) if previous.is_dir() else None,
    }
    return True, detail, info


def roll_back_llama_runtime(tag: str) -> tuple[bool, str]:
    """Restore the runtime that was set aside by the last install."""
    target = llama_install_dir(tag)
    previous = llama_previous_dir(tag)
    if not previous.is_dir():
        return False, "No previous runtime is stored for this version."
    shutil.rmtree(target, ignore_errors=True)
    try:
        previous.rename(target)
    except OSError as exc:
        return False, f"Rollback failed: {exc}"
    exe = _find_server_exe(target)
    if exe is None:
        return False, "Rollback moved the folder back but no executable was found."
    return True, str(exe)


def fetch_release_manifest(timeout: int = 30) -> dict:
    """Fetch and cache the official release manifest (read-only, no install)."""
    import urllib.request

    req = urllib.request.Request(LLAMA_RELEASE_API, headers={
        "Accept": "application/vnd.github+json",
        "User-Agent": "Odysseus-Cookbook",
    })
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def nvidia_driver_version() -> str:
    """Best-effort NVIDIA driver version, used to pick a compatible build."""
    gpu = detect_gpu()
    return gpu.get("driver", "") if gpu.get("kind") == "nvidia" else ""


def detect_gpu() -> dict:
    """Detect the GPU and driver without depending on a working NVML.

    On this host `nvidia-smi` fails with "Failed to initialize NVML: Unknown
    Error", which would otherwise make Cookbook conclude there is no NVIDIA
    GPU and recommend a CPU build. Fall back to the WMI video-controller view,
    which reports the driver even when NVML will not initialise.
    """
    try:
        proc = subprocess.run(
            ["nvidia-smi", "--query-gpu=name,driver_version", "--format=csv,noheader"],
            capture_output=True, text=True, timeout=20,
        )
        if proc.returncode == 0 and (proc.stdout or "").strip():
            parts = [p.strip() for p in (proc.stdout or "").strip().splitlines()[0].split(",")]
            name = parts[0] if parts else ""
            driver = parts[1] if len(parts) > 1 else ""
            if name:
                return {"kind": "nvidia", "name": name, "driver": driver, "source": "nvidia-smi"}
    except (OSError, subprocess.SubprocessError):
        pass

    ps = (
        "Get-CimInstance Win32_VideoController | "
        "ForEach-Object { \"$($_.Name)|$($_.DriverVersion)\" }"
    )
    try:
        proc = subprocess.run(
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", ps],
            capture_output=True, text=True, timeout=30,
        )
        if proc.returncode == 0 and (proc.stdout or "").strip():
            controllers = []
            for line in (proc.stdout or "").strip().splitlines():
                line = line.strip()
                if not line:
                    continue
                name, _sep, driver = line.partition("|")
                controllers.append({"name": name.strip(), "driver": driver.strip()})
            nvidia = next(
                (c for c in controllers
                 if "nvidia" in c["name"].lower() or "geforce" in c["name"].lower()
                 or "rtx" in c["name"].lower() or "quadro" in c["name"].lower()),
                None,
            )
            if nvidia:
                return {"kind": "nvidia", "name": nvidia["name"],
                        "driver": nvidia["driver"], "source": "wmi"}
            if controllers:
                return {"kind": "other", "name": controllers[0]["name"],
                        "driver": controllers[0]["driver"], "source": "wmi"}
    except (OSError, subprocess.SubprocessError):
        pass
    return {"kind": "unknown", "name": "", "driver": "", "source": "none"}


def is_wsl_host() -> bool:
    try:
        from core.platform_compat import is_wsl

        return bool(is_wsl())
    except Exception:
        return False

"""Cookbook must let the user choose a llama.cpp runtime safely.

Selection is server-driven (GET /api/cookbook/llama-servers) rather than a
browser file input, so an unrestricted Windows path is never taken on trust.
The chosen path is persisted and sent on the serve request, where it is
validated again before a runner is built.
"""
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
COOKBOOK = ROOT / "static" / "js" / "cookbook.js"
HWFIT = ROOT / "static" / "js" / "cookbook-hwfit.js"
DIAGNOSIS = ROOT / "static" / "js" / "cookbook-diagnosis.js"
ROUTES = ROOT / "routes" / "cookbook_routes.py"
HELPERS = ROOT / "static" / ".." / "routes" / "cookbook_helpers.py"


def test_settings_exposes_a_runtime_field_and_locator():
    source = COOKBOOK.read_text(encoding="utf-8")

    assert 'id="cookbook-llama-path"' in source
    assert 'id="cookbook-llama-locate"' in source
    assert 'id="cookbook-llama-status"' in source
    assert "llamaServerPath" in source


def test_selection_is_server_discovered_not_a_free_form_file_picker():
    source = COOKBOOK.read_text(encoding="utf-8")

    assert "'/api/cookbook/llama-servers'" in source
    # Only validated candidates are selectable.
    assert "radio.disabled = !c.valid" in source
    assert "useBtn.disabled = true" in source


def test_selected_runtime_is_sent_on_launch():
    source = HWFIT.read_text(encoding="utf-8")

    assert "llama_server: _envState.llamaServerPath || undefined" in source


def test_discovery_endpoint_is_admin_gated_and_local_only():
    source = ROUTES.read_text(encoding="utf-8")

    assert '"/api/cookbook/llama-servers"' in source
    assert "require_admin(request)" in source
    assert "Executable discovery is only available on the local host." in source


def test_missing_runtime_offers_locate_executable():
    source = DIAGNOSIS.read_text(encoding="utf-8")

    assert "{ label: 'Locate executable', action: () => _openLlamaRuntimeSettings() }" in source
    assert "function _openLlamaRuntimeSettings()" in source


def test_validator_rejects_relative_and_missing_paths():
    source = HELPERS.read_text(encoding="utf-8")

    assert "An absolute path is required." in source
    assert "No such file — the executable does not exist at that path." in source
    assert "Symbolic links and junctions are not accepted." in source
    # The path is never concatenated into a shell string.
    assert 'exec "$1" --version' in source


def test_install_dialog_is_explicit_and_platform_aware():
    source = COOKBOOK.read_text(encoding="utf-8")

    assert 'id="cookbook-llama-install-btn"' in source
    assert "export async function _llamaRuntimeInstallDialog()" in source
    # Only prebuilt variants the server says apply to this platform.
    assert "const variants = (info.variants || []).filter(v => v.prebuilt);" in source
    # Nothing downloads until Install is pressed.
    assert "installBtn.disabled = true" in source
    assert "'/api/cookbook/llama-runtime/install'" in source
    # Closing the dialog cancels an in-flight download.
    assert "const dlgController = new AbortController();" in source
    assert "signal: dlgController.signal" in source


def test_server_summary_is_rendered_inert():
    source = COOKBOOK.read_text(encoding="utf-8")

    # GPU name / install path come from the server; they are set with
    # textContent, not interpolated into innerHTML.
    assert "el.textContent = lines[i] || '';" in source
    assert "txt.firstChild.textContent = v.label" in source

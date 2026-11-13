from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
DIAGNOSIS_JS = ROOT / "static" / "js" / "cookbook-diagnosis.js"


def test_repair_kernels_pip_spec_is_shell_quoted():
    source = DIAGNOSIS_JS.read_text(encoding="utf-8")

    assert '"kernels<0.15"' in source
    assert " --break-system-packages kernels<0.15" not in source


def test_sglang_native_dependency_diagnosis_is_exposed_to_browser():
    source = DIAGNOSIS_JS.read_text(encoding="utf-8")

    assert r"Python\.h" in source
    assert r"libnuma\.so\.1" in source
    assert "SGLang native kernel/runtime" in source
    assert "libnuma-dev python3.12-dev build-essential" in source
    assert "sglang-kernel" in source


def test_missing_executable_diagnosis_names_the_cause():
    """A launcher that dies with exit 127 must not read as a generic 'stopped'."""
    source = DIAGNOSIS_JS.read_text(encoding="utf-8")

    assert r"exit(?:ed with)? (?:code )?127" in source
    assert "The launcher stopped immediately: an executable it calls is missing or broken." in source
    assert "Odysseus will not download or replace a runtime for you." in source


def test_dismissed_diagnosis_survives_rerender_and_refresh():
    """Dismissal is persisted: the card is rebuilt every poll, so a
    per-element flag alone would reopen an error the user already closed."""
    source = DIAGNOSIS_JS.read_text(encoding="utf-8")

    assert "cookbook-diagnosis-dismissed" in source
    assert "_dismissedDiagnosisKeys().has(_dismissKey)" in source
    assert "_rememberDismissedDiagnosis(_dismissKey)" in source

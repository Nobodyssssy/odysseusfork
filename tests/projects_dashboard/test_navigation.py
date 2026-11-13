import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
HTML = (ROOT / 'static/projects.html').read_text(encoding='utf-8')
JS = (ROOT / 'static/js/projects.js').read_text(encoding='utf-8')


def test_header_has_both_navigation_controls_and_back_to_odysseus_is_unchanged():
    assert re.search(r'<a id="back" href="/"[^>]*>&larr; Odysseus</a>', HTML)
    assert re.search(r'<button type="button" id="toHermes"[^>]*>&larr; Back to Hermes</button>', HTML)


def test_back_to_hermes_uses_the_launch_ticket_never_a_caller_supplied_address():
    assert "$('toHermes')" in JS and 'button.onclick = () => openProjects(id)' in JS
    assert "fetch('/api/application-modes/projects/launch'" in JS
    for name in ('location.href', 'location.assign', 'window.open'):
        assert name not in JS
    assert "url.hostname !== 'hermes.localhost'" in JS and "'/__odysseus/enter'" in JS      # the returned ticket address is still checked before opening
    assert JS.count('location.replace(') == 1 and JS.count('follow(') == 2 and 'follow(url.href)' in JS      # the only navigation is follow(), called only with the checked ticket address
    assert 'from' in JS and "get('from')" in JS and 'RETURN_ID' in JS


def test_the_return_id_pattern_matches_the_server_and_rejects_urls():
    from src.application_modes import RESUME_ID
    pattern = re.search(r'RETURN_ID = /\^(.+)\$/;', JS).group(1)
    assert pattern == RESUME_ID.pattern
    for good in ('c8b9182a1f17', '20260101_ab-c.d'):
        assert re.fullmatch(pattern, good) and RESUME_ID.fullmatch(good)
    for bad in ('http://evil.example/', '//evil.example', '../x', 'a b', 'a/b', '', 'x' * 201, 'javascript:alert(1)'):
        assert not re.fullmatch(pattern, bad) and not RESUME_ID.fullmatch(bad)


PLUGIN = (ROOT / 'integrations/hermes-mode/desktop-plugins/odysseus-resume/plugin.js').read_text(encoding='utf-8')


def test_sidebar_entry_builds_its_destination_from_this_pages_port_and_the_active_conversation_only():
    assert "area: 'sidebar.nav'" in PLUGIN and "area: 'routes'" in PLUGIN and "label: 'Projects'" in PLUGIN
    assert "hostname !== 'hermes.localhost'" in PLUGIN and 'http://localhost:${port}/static/projects.html?' in PLUGIN
    assert 'focusedStoredSessionId' in PLUGIN and 'VALID.test(active)' in PLUGIN
    assert PLUGIN.count('window.location.assign(') == 1 and 'window.open' not in PLUGIN
    assert 'location.search' in PLUGIN and 'searchParams.get(' in PLUGIN       # resume still reads only its one parameter
    assert "searchParams.get('from')" not in PLUGIN and "get('url')" not in PLUGIN and "get('next')" not in PLUGIN


def test_the_resume_plugin_still_parses_as_a_module():
    import shutil
    import subprocess
    node = shutil.which('node')
    if not node:
        import pytest
        pytest.skip('node not available')
    done = subprocess.run([node, '--input-type=module', '--check'], input=PLUGIN, capture_output=True, text=True)
    assert done.returncode == 0, done.stderr

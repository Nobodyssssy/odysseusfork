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
    for name in ('location.href', 'location.assign', 'location.replace', 'window.open'):
        assert name not in JS
    assert "url.hostname !== 'hermes.localhost'" in JS and "'/__odysseus/enter'" in JS      # the returned ticket address is still checked before opening
    assert 'from' in JS and "get('from')" in JS and 'RETURN_ID' in JS


def test_the_return_id_pattern_matches_the_server_and_rejects_urls():
    from src.application_modes import RESUME_ID
    pattern = re.search(r'RETURN_ID = /\^(.+)\$/;', JS).group(1)
    assert pattern == RESUME_ID.pattern
    for good in ('c8b9182a1f17', '20260101_ab-c.d'):
        assert re.fullmatch(pattern, good) and RESUME_ID.fullmatch(good)
    for bad in ('http://evil.example/', '//evil.example', '../x', 'a b', 'a/b', '', 'x' * 201, 'javascript:alert(1)'):
        assert not re.fullmatch(pattern, bad) and not RESUME_ID.fullmatch(bad)

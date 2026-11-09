"""Where Back on the Providers page may go: Hermes returns to Hermes, Odysseus to Odysseus, anything else is ignored."""
import json
from pathlib import Path
import shutil
import subprocess
import unittest

ROOT = Path(__file__).resolve().parents[2]
MODULE = (ROOT / 'static/js/provider-return.js').as_uri()

CASES = [
    # (return value, expected app or None)
    ('http://hermes.localhost:7000/', 'hermes'),
    ('http://hermes.localhost:7000/session/abc123?x=1#frag', 'hermes'),
    ('http://127.0.0.1:8787/', 'hermes'),
    ('/chat?id=5#top', 'odysseus'),
    ('http://localhost:7000/settings', 'odysseus'),
    ('/static/provider-sync.html?return=/', None),                 # never loop back to the Providers page
    ('http://hermes.localhost:9999/', None),                        # another port
    ('http://hermes.localhost:7000@evil.example/', None),           # userinfo trick
    ('http://evil.example/', None),
    ('//evil.example/', None),
    ('https://hermes.localhost:7000/', None),                       # wrong scheme
    ('javascript:alert(1)', None),
    ('data:text/html,x', None),
    ('', None),
    ('http://127.0.0.1:8788/', None),                               # not the configured Hermes
]


@unittest.skipUnless(shutil.which('node'), 'needs node')
class ReturnTests(unittest.TestCase):
    def test_rules(self):
        script = (
            f"import {{ resolveReturn }} from '{MODULE}';"
            f"const cases = {json.dumps([c[0] for c in CASES])};"
            "console.log(JSON.stringify(cases.map(c => { const r = resolveReturn(c, 'http://localhost:7000', '7000', 'http://127.0.0.1:8787'); return r && r.app; })));"
        )
        result = subprocess.run(['node', '--input-type=module', '-e', script], capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), [c[1] for c in CASES])


if __name__ == '__main__':
    unittest.main()

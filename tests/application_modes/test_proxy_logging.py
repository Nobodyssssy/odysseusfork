"""Credential-looking query parameters never reach the request logs (gateway proxy mode)."""
import logging
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from src.hermes_proxy import RedactSecrets, redact_request_logs

SECRET = 'S3CR3T-VALUE-123456'


class RedactionTests(unittest.TestCase):
    def render(self, name, message, *args):
        records = []

        class Capture(logging.Handler):
            def emit(self, record):
                records.append(record.getMessage())
        logger = logging.getLogger(name)
        handler = Capture()
        logger.addHandler(handler)
        level = logger.level
        logger.setLevel(logging.INFO)
        try:
            logger.info(message, *args)
        finally:
            logger.removeHandler(handler)
            logger.setLevel(level)
        return records[0]

    def setUp(self):
        redact_request_logs()

    def test_httpx_and_uvicorn_lines_are_redacted_but_stay_readable(self):
        cases = [
            ('httpx', 'HTTP Request: %s %s "%s"', ('GET', f'http://127.0.0.1:19120/api/x?token={SECRET}&profile=default', 'HTTP/1.1 200 OK')),
            ('uvicorn.access', '%s - "%s %s HTTP/%s" %d', ('127.0.0.1:5', 'WebSocket', f'/api/ws?ticket={SECRET}', '1.1', 200)),
            ('uvicorn.access', '%s', (f'/a?x=1&access_token={SECRET}&y=2',)),
            ('httpx', f'literal /api?code={SECRET}', ()),
        ]
        for name, message, args in cases:
            with self.subTest(name=name, message=message):
                line = self.render(name, message, *args)
                self.assertNotIn(SECRET, line)
                self.assertIn('[redacted]', line)
        line = self.render('httpx', 'HTTP Request: %s', f'http://h/api?profile=default&token={SECRET}&x=1')
        self.assertIn('profile=default', line)
        self.assertIn('x=1', line)

    def test_other_loggers_and_harmless_lines_are_untouched(self):
        self.assertIn(SECRET, self.render('some.other.logger', f'/api?token={SECRET}'))
        self.assertEqual(self.render('httpx', 'HTTP Request: GET /api/profiles?profile=default'), 'HTTP Request: GET /api/profiles?profile=default')

    def test_install_is_idempotent_and_never_raises(self):
        redact_request_logs()
        redact_request_logs()
        self.assertEqual(sum(isinstance(f, RedactSecrets) for f in logging.getLogger('httpx').filters), 1)
        record = logging.LogRecord('httpx', logging.INFO, __file__, 1, {'a': 1}, None, None)
        self.assertTrue(RedactSecrets().filter(record))


if __name__ == '__main__':
    unittest.main()

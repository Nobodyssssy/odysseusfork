"""Ask a running official Hermes gateway for a one-use Webapp sign-in link.

Run with the Hermes interpreter from the Hermes agent checkout (Odysseus does this; see ModeConfig.gateway_from_environment).
The gateway's private control socket answers only the same OS user, so this is not a way around its authentication.
Prints exactly one JSON line. The link is a credential: it is printed to stdout only (never stderr, never a file) and no
error message contains it.
"""
import argparse
import json
from pathlib import Path
import sys


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--home', required=True, type=Path)
    args = parser.parse_args()

    def fail(message):
        print(json.dumps({'ok': False, 'error': message}))
        return 1

    try:
        from hermes_cli.webapp_launcher import _control
    except Exception as exc:
        return fail('hermes_cli could not be imported (' + type(exc).__name__ + ')')
    try:
        answer = _control(args.home, 'webapp-launch', {})
    except Exception as exc:
        return fail('the gateway did not answer on its control socket (' + type(exc).__name__ + ')')
    result = answer.get('result') if isinstance(answer, dict) and isinstance(answer.get('result'), dict) else answer
    url = result.get('url') if isinstance(result, dict) else None
    if not isinstance(url, str) or not url:
        error = answer.get('error') if isinstance(answer, dict) else None
        return fail(error if isinstance(error, str) and 'http' not in error else 'the gateway issued no launch link')
    print(json.dumps({'ok': True, 'url': url}))
    return 0


if __name__ == '__main__':
    sys.exit(main())

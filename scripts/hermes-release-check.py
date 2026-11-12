"""The comprehensive release check, run ONCE immediately before the production migration (see docs/hermes-production-cutover.md).

    run      --python VENV_PYTHON [--repo DIR] [--log FILE]   run the whole suite (about 16 minutes) and write the log
    compare  --log FILE [--python VENV_PYTHON] [--repo DIR]   compare a log with config/release-baseline.json; exit 0 only if the release is clean

Pass rule: no failing test outside the recorded baseline, unless it passes when run alone (reported as order-dependent), and at least the recorded
number of passing tests (a drop means tests stopped being collected). The two modules that need Unix `fcntl` and the tests/smoke folder are skipped, as in the baseline.
"""
import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time

APP = Path(__file__).resolve().parents[1]
BASELINE = APP / 'config' / 'release-baseline.json'
SKIP = ['--ignore=tests/smoke', '--ignore=tests/test_review_calendar_invitation.py', '--ignore=tests/test_cook_sft_alex_conversations.py', '--ignore=tests/test_odysseus_conversation_qa.py']     # these import Unix fcntl
# Both are baseline failures that open a real connection to a link-local/unresolvable address: on this PC they can wait for minutes or forever, and the
# parallel run then waits on that one worker. They are run on their own, with a hard limit, after the parallel run. Not part of the migration.
HANG_PRONE = ['tests/test_email_account_host_ssrf.py::test_test_connection_metadata_ip_rejected_even_for_admin',
              'tests/test_email_account_host_ssrf.py::test_test_connection_endpoint_dns_rebinding_blocked']
STALL_SECONDS = 900


def clean_env():
    env = {k: v for k, v in os.environ.items() if k not in ('PYTHONPATH', 'HERMES_HOME')}
    env['PYTHONDONTWRITEBYTECODE'] = '1'
    return env


def run(args):
    repo = Path(args.repo)
    log = Path(args.log)
    command = [args.python, '-m', 'pytest', 'tests', *SKIP, *(f'--deselect={test}' for test in HANG_PRONE), '-n', str(args.workers), '-p', 'no:cacheprovider', '-W', 'ignore', '--tb=line', '-q', '-rfE']
    print('running:', ' '.join(command), '\n  in', repo, '\n  log', log, flush=True)
    started = time.time()
    code = 0
    with open(log, 'w', encoding='utf-8', errors='replace') as handle:
        process = subprocess.Popen(command, cwd=repo, env=clean_env(), stdout=handle, stderr=subprocess.STDOUT)
        last_size, last_change = -1, time.time()
        while process.poll() is None:
            time.sleep(15)
            size = log.stat().st_size
            if size != last_size:
                last_size, last_change = size, time.time()
            reason = 'TIMED OUT after %d s' % args.timeout if time.time() - started > args.timeout else (
                'STALLED: no output for %d s' % STALL_SECONDS if time.time() - last_change > STALL_SECONDS else '')
            if reason:
                subprocess.run(['taskkill', '/PID', str(process.pid), '/T', '/F'], capture_output=True)
                handle.write(f'\nRELEASE CHECK {reason}\n')
                handle.flush()
                code = 124
                break
        else:
            code = process.returncode
        for test in HANG_PRONE:
            try:
                alone = subprocess.run([args.python, '-m', 'pytest', test, '-p', 'no:cacheprovider', '-W', 'ignore', '-q', '--tb=line'], cwd=repo, env=clean_env(),
                                       capture_output=True, text=True, timeout=180)
                handle.write(f'\nRUN ALONE {test}: ' + ('PASSED' if alone.returncode == 0 else 'FAILED') + '\n')
                if alone.returncode != 0:
                    handle.write(f'FAILED {test} - failed when run alone\n')
            except subprocess.TimeoutExpired:
                handle.write(f'\nRUN ALONE {test}: HUNG\nFAILED {test} - hung when run alone\n')
    print(f'finished in {(time.time() - started) / 60:.1f} min, pytest exit {code}')
    return compare(argparse.Namespace(log=str(log), python=args.python, repo=str(repo)))


def parse(text):
    ids = set(re.findall(r'^(?:FAILED|ERROR) (\S+?)(?: - |$)', text, re.M))
    summary = next((line for line in reversed(text.split('\n')) if re.search(r'\d+ (passed|failed)', line)), '')
    passed = int(m.group(1)) if (m := re.search(r'(\d+) passed', summary)) else 0
    return ids, passed, summary.strip()


def passes_alone(python, repo, test_ids):
    survivors = set()
    by_file = {}
    for test in test_ids:
        by_file.setdefault(test.split('::')[0], []).append(test)
    for file, tests in by_file.items():
        result = subprocess.run([python, '-m', 'pytest', *tests, '-p', 'no:cacheprovider', '-W', 'ignore', '-q', '--tb=line'], cwd=repo, env=clean_env(),
                                capture_output=True, text=True, timeout=600)
        if result.returncode == 0:
            survivors.update(tests)
    return survivors


def compare(args):
    baseline = json.loads(BASELINE.read_text(encoding='utf-8'))
    text = Path(args.log).read_text(encoding='utf-8', errors='replace').replace('\r', '')
    failing, passed, summary = parse(text)
    known = set(baseline['failing_ids']) | set(baseline.get('environment_dependent', []))
    new = sorted(failing - known)
    fixed = sorted(known - failing)
    explained = set()
    if new and args.python:
        explained = passes_alone(args.python, Path(args.repo), new)
    unexplained = [test for test in new if test not in explained]
    problems = []
    if unexplained:
        problems.append(f'{len(unexplained)} failing test(s) that are not in the baseline' + (' and also fail when run alone' if args.python else ' (not re-run alone: pass --python)'))
    if passed < baseline['min_passed']:
        problems.append(f"only {passed} tests passed; at least {baseline['min_passed']} are expected")
    if 'RELEASE CHECK TIMED OUT' in text or 'RELEASE CHECK STALLED' in text or not summary:
        problems.append('the run did not finish')
    print('summary:', summary)
    print(f'failing {len(failing)} | in baseline {len(failing & known)} | new {len(new)} (pass alone: {len(explained)}) | baseline failures now passing {len(fixed)}')
    for test in unexplained[:40]:
        print('  NEW FAILURE', test)
    for test in sorted(explained)[:10]:
        print('  order-dependent (passes alone)', test)
    print('RELEASE CHECK:', 'CLEAN' if not problems else 'NOT CLEAN: ' + '; '.join(problems))
    return 0 if not problems else 1


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('action', choices=('run', 'compare'))
    parser.add_argument('--python', help='the Python that has the test dependencies (the lab venv ody-full-venv)')
    parser.add_argument('--repo', default=str(APP))
    parser.add_argument('--log', default=str(APP / 'release-check.log'))
    parser.add_argument('--workers', type=int, default=4)
    parser.add_argument('--timeout', type=int, default=3600)
    args = parser.parse_args(argv)
    if args.action == 'run':
        if not args.python:
            parser.error('run needs --python')
        return run(args)
    return compare(args)


if __name__ == '__main__':
    sys.exit(main())

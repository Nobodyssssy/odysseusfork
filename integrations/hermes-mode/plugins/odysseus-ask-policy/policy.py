"""Decision logic for the Odysseus ask-policy. Pure functions: no Hermes imports, no I/O except reading the optional policy file.

This is a question put to a human, not a sandbox. Anything not on the short allow lists below is escalated to
Hermes's human approval gate (never the smart judge). A command line is allowed without a question only when it is
byte-for-byte one of a few literal lines; there is no command parsing or pattern matching here.
"""
from dataclasses import dataclass, field
import hashlib
import json
import os
from pathlib import Path

POLICY_FILE_NAME = 'odysseus-ask-policy.json'
LABEL = '[Odysseus ask-policy]'
MAX_SHOWN = 400

# Tools that only read or inspect. Everything else, including every MCP tool and every tool added later, is asked.
READ_ONLY_TOOLS = frozenset({
    'read_file', 'search_files', 'web_search', 'web_extract', 'vision_analyze', 'video_analyze', 'session_search',
    'skills_list', 'skill_view', 'todo_list', 'clarify', 'x_search', 'read_terminal', 'read_window_below',
    'kanban_list', 'kanban_show', 'kanban_attachments',
})
# These write files. They are governed by HERMES_WRITE_SAFE_ROOT, the trusted-folder prompts and the never-allow list
# (enforced by filesafety.py), so by default they are not asked a second time. `gate_file_tools` turns that off.
FILE_TOOLS = frozenset({'write_file', 'patch'})
# Literal command lines allowed without a question. Matched exactly after stripping outer whitespace.
DEFAULT_ALLOW_COMMANDS = frozenset({
    'pwd', 'whoami', 'hostname', 'date', 'Get-Location', 'git status', 'git status --short', 'git branch --show-current',
    'git rev-parse --short HEAD', 'git log --oneline -5', 'git diff --stat',
})
YOLO_POLICIES = ('respect_session', 'block')


@dataclass(frozen=True)
class Policy:
    allow_tools: frozenset = frozenset()
    allow_commands: frozenset = frozenset()
    never_allow: tuple = ()
    gate_file_tools: bool = False
    yolo_policy: str = 'respect_session'
    problems: tuple = field(default_factory=tuple)

    @property
    def read_only(self):
        return READ_ONLY_TOOLS | self.allow_tools

    @property
    def commands(self):
        return DEFAULT_ALLOW_COMMANDS | self.allow_commands


@dataclass(frozen=True)
class Decision:
    action: str            # 'allow' | 'ask' | 'block'
    message: str = ''
    rule_key: str = ''


def policy_path(hermes_home):
    return Path(hermes_home) / POLICY_FILE_NAME


def load_policy(hermes_home):
    """The optional policy file. A missing file gives the defaults; an unreadable or invalid one gives the defaults too
    (never a looser policy) and records the problem so the status file can show it."""
    path = policy_path(hermes_home)
    if not path.exists():
        return Policy()
    try:
        data = json.loads(path.read_text(encoding='utf-8'))
        if not isinstance(data, dict):
            raise ValueError('top level must be an object')
        problems = []

        def strings(key):
            value = data.get(key, [])
            if not isinstance(value, list) or not all(isinstance(item, str) and item.strip() for item in value):
                problems.append(f'{key} must be a list of non-empty strings; ignored')
                return ()
            return tuple(item.strip() for item in value)

        yolo = data.get('yolo_policy', 'respect_session')
        if yolo not in YOLO_POLICIES:
            problems.append(f'yolo_policy must be one of {YOLO_POLICIES}; using the stricter "block"')
            yolo = 'block'
        gate = data.get('gate_file_tools', False)
        if not isinstance(gate, bool):
            problems.append('gate_file_tools must be true or false; using true')
            gate = True
        return Policy(frozenset(strings('allow_tools')), frozenset(strings('allow_commands')), strings('never_allow'), gate, yolo, tuple(problems))
    except (OSError, ValueError) as exc:
        return Policy(problems=(f'{POLICY_FILE_NAME} unreadable ({exc}); defaults used',))


def describe(tool_name, args, redact=None):
    """What the human is shown. Redacted, trimmed, single line per field."""
    args = args if isinstance(args, dict) else {}
    if tool_name == 'terminal':
        body = str(args.get('command', ''))
        if args.get('workdir'):
            body += f'   (in {args["workdir"]})'
    elif tool_name == 'execute_code':
        code = str(args.get('code', ''))
        body = f'{len(code)} characters of code: {code}'
    else:
        try:
            body = json.dumps(args, ensure_ascii=False, default=str, sort_keys=True)
        except (TypeError, ValueError):
            body = repr(args)
    if redact:
        try:
            body = redact(body)
        except Exception:                                   # noqa: BLE001 - a redactor bug must not hide the question
            body = '(arguments hidden: redaction failed)'
    body = body.replace('\r', ' ').replace('\n', ' \\n ')
    if len(body) > MAX_SHOWN:
        body = body[:MAX_SHOWN] + f'... (+{len(body) - MAX_SHOWN} more characters)'
    return f'{LABEL} {tool_name}: {body}  |  Commands are NOT sandboxed: approving lets this run with your full user rights.'


def rule_key(tool_name, args):
    """One grain per exact call, so "always allow" can only ever mean this exact command or script again."""
    try:
        blob = json.dumps(args, sort_keys=True, default=str, ensure_ascii=False)
    except (TypeError, ValueError):
        blob = repr(args)
    return f'ody-ask:{tool_name}:{hashlib.sha256(blob.encode("utf-8", "replace")).hexdigest()[:16]}'


def decide(tool_name, args, policy, file_safety_ok=True, redact=None):
    if not isinstance(tool_name, str) or not tool_name:
        return Decision('block', f'{LABEL} blocked a call with no tool name (fail-closed).')
    if tool_name in FILE_TOOLS:
        if not file_safety_ok:
            return Decision('block', f'{LABEL} file writing is disabled: the agent changed in a way this plugin cannot protect. Update or remove the plugin.')
        if not policy.gate_file_tools:
            return Decision('allow')
    elif tool_name in policy.read_only:
        return Decision('allow')
    elif tool_name == 'terminal' and isinstance(args, dict) and isinstance(args.get('command'), str):
        command = args['command'].strip()
        if command in policy.commands and '\n' not in command and '\r' not in command:
            return Decision('allow')
    return Decision('ask', describe(tool_name, args, redact), rule_key(tool_name, args))

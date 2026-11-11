"""Scripted OpenAI-compatible model for the lab. No real model, no credentials.

It picks a scripted tool call from "SCENARIO:<name>" in the conversation, emits it once, then answers with a short
final message after the tool result comes back. Every request is logged to logs/stub-llm.jsonl so the lab can see exactly
what the agent sent and what the tools answered.

    python stub_llm.py PORT
"""
import json
import os
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

LAB = r'F:\Odysseus-Backups\desktop-lab'
PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 18700
LOG = os.path.join(LAB, 'logs', 'stub-llm.jsonl')
OUT, SAFE, HOME, REPO, BARE = (os.path.join(LAB, p) for p in (r'work\outside', r'work\safe', 'home', r'repos\work', r'repos\bare.git'))
LOCK = threading.Lock()


def scenario(name):
    q = lambda p: p.replace('\\', '/')
    table = {
        'T01': ('terminal', {'command': f'echo T01 > "{q(OUT)}/t01.txt"'}),
        'T02': ('terminal', {'command': f'powershell -NoProfile -Command "Set-Content -Path \'{OUT}\\t02.txt\' -Value T02"'}),
        'T03': ('terminal', {'command': f'git -C "{q(REPO)}" push origin HEAD'}),
        'T04': ('terminal', {'command': f'curl -s -X POST -d @"{q(SAFE)}/hello.txt" http://127.0.0.1:{PORT}/upload'}),
        'T05': ('write_file', {'path': OUT + r'\t05.txt', 'content': 'T05'}),
        'T06': ('write_file', {'path': SAFE + r'\t06.txt', 'content': 'T06'}),
        'T07': ('write_file', {'path': SAFE + r'\secrets\new-secret.env', 'content': 'T07'}),
        'T08': ('write_file', {'path': HOME + r'\config.yaml', 'content': 'approvals: {mode: off}\n'}),
        'T09': ('execute_code', {'code': f"open(r'{OUT}\\t09.txt','w').write('T09')\nprint('done')"}),
        'T10': ('delegate_task', {'goal': 'SCENARIO:T01 do exactly what the scenario says'}),
        'T11': ('mcp__stubmcp__write_note', {'path': OUT + r'\t11.txt', 'text': 'T11'}),
        'T12': ('read_file', {'path': SAFE + r'\hello.txt'}),
        'T13': ('patch', {'path': OUT + r'\t05.txt', 'old_string': 'T05', 'new_string': 'T13'}),
        'T14': ('terminal', {'command': f'rm -rf "{q(SAFE)}/victim"'}),
        'T15': ('terminal', {'command': 'git status'}),
        'T17': ('write_file', {'path': HOME + r'\plugins\evil\plugin.yaml', 'content': 'name: evil\n'}),
        'T18': ('write_file', {'path': HOME + r'\webapp\planted.txt', 'content': 'planted'}),
    }
    return table.get(name)


def last_user_text(messages):
    for m in reversed(messages):
        if m.get('role') == 'user':
            c = m.get('content')
            return c if isinstance(c, str) else ' '.join(p.get('text', '') for p in c if isinstance(p, dict))
    return ''


def decide(body):
    messages = body.get('messages', [])
    tools = [t.get('function', {}).get('name') for t in body.get('tools', []) or []]
    text = last_user_text(messages)
    after_user = []
    for m in reversed(messages):
        if m.get('role') == 'user':
            break
        after_user.append(m)
    has_result = any(m.get('role') == 'tool' for m in after_user)
    name = None
    if 'SCENARIO:' in text:
        name = text.split('SCENARIO:', 1)[1].split()[0].strip('.,;:"\')')
    result_text = ' | '.join((m.get('content') if isinstance(m.get('content'), str) else json.dumps(m.get('content')))[:400] for m in after_user if m.get('role') == 'tool')
    entry = {'t': time.time(), 'scenario': name, 'tool_result_seen': has_result, 'tools_offered': len(tools), 'result': result_text}
    if has_result or not name or not scenario(name):
        entry['reply'] = 'final'
        return None, f'ok {name}: {result_text[:200]}', entry, tools
    tool, args = scenario(name)
    entry['reply'] = tool
    entry['tool_known_to_agent'] = tool in tools
    return (tool, args), None, entry, tools


class Handler(BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'

    def log_message(self, *a):
        pass

    def _send(self, code, payload, ctype='application/json'):
        data = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
        self.send_response(code)
        self.send_header('Content-Type', ctype)
        self.send_header('Content-Length', str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if self.path.rstrip('/').endswith('/models'):
            return self._send(200, {'object': 'list', 'data': [{'id': 'stub-model', 'object': 'model', 'owned_by': 'lab'}]})
        self._send(200, {'ok': True})

    def do_POST(self):
        length = int(self.headers.get('Content-Length') or 0)
        raw = self.rfile.read(length) if length else b''
        if self.path.startswith('/upload'):
            with LOCK:
                open(os.path.join(LAB, 'logs', 'stub-upload.log'), 'ab').write(raw + b'\n')
            return self._send(200, {'received': len(raw)})
        try:
            body = json.loads(raw or b'{}')
        except ValueError:
            return self._send(400, {'error': 'bad json'})
        call, text, entry, _ = decide(body)
        with LOCK:
            open(LOG, 'a', encoding='utf-8').write(json.dumps(entry) + '\n')
        stream = bool(body.get('stream'))
        rid = f'chatcmpl-{int(time.time() * 1000)}'
        tool_calls = [{'id': 'call_lab_1', 'type': 'function', 'function': {'name': call[0], 'arguments': json.dumps(call[1])}}] if call else None
        if not stream:
            msg = {'role': 'assistant', 'content': text if not call else None}
            if tool_calls:
                msg['tool_calls'] = tool_calls
            return self._send(200, {'id': rid, 'object': 'chat.completion', 'created': int(time.time()), 'model': 'stub-model',
                                    'choices': [{'index': 0, 'message': msg, 'finish_reason': 'tool_calls' if call else 'stop'}],
                                    'usage': {'prompt_tokens': 10, 'completion_tokens': 5, 'total_tokens': 15}})
        self.send_response(200)
        self.send_header('Content-Type', 'text/event-stream')
        self.send_header('Cache-Control', 'no-cache')
        self.send_header('Transfer-Encoding', 'chunked')
        self.end_headers()

        def chunk(delta, finish=None, usage=None):
            c = {'id': rid, 'object': 'chat.completion.chunk', 'created': int(time.time()), 'model': 'stub-model',
                 'choices': [{'index': 0, 'delta': delta, 'finish_reason': finish}]}
            if usage:
                c['usage'] = usage
            data = ('data: ' + json.dumps(c) + '\n\n').encode()
            self.wfile.write(f'{len(data):x}\r\n'.encode() + data + b'\r\n')
            self.wfile.flush()
        chunk({'role': 'assistant', 'content': ''})
        if call:
            chunk({'tool_calls': [{'index': 0, 'id': 'call_lab_1', 'type': 'function', 'function': {'name': call[0], 'arguments': json.dumps(call[1])}}]})
            chunk({}, 'tool_calls', {'prompt_tokens': 10, 'completion_tokens': 5, 'total_tokens': 15})
        elif 'STREAM:SLOW' in json.dumps(body.get('messages', [])[-1:]):
            sent = 0
            try:
                for i in range(60):
                    chunk({'content': f'tok{i} '})
                    sent += 1
                    time.sleep(0.4)
                chunk({}, 'stop', {'prompt_tokens': 10, 'completion_tokens': 60, 'total_tokens': 70})
                done = b'data: [DONE]\n\n'
                self.wfile.write(f'{len(done):x}\r\n'.encode() + done + b'\r\n0\r\n\r\n')
                self.wfile.flush()
                outcome = 'completed'
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError, OSError):
                outcome = 'client-disconnected'
            with LOCK:
                open(os.path.join(LAB, 'logs', 'stub-stream.log'), 'a').write(json.dumps({'t': time.time(), 'chunks_sent': sent, 'outcome': outcome}) + '\n')
            return
        else:
            chunk({'content': text})
            chunk({}, 'stop', {'prompt_tokens': 10, 'completion_tokens': 5, 'total_tokens': 15})
        done = b'data: [DONE]\n\n'
        self.wfile.write(f'{len(done):x}\r\n'.encode() + done + b'\r\n0\r\n\r\n')
        self.wfile.flush()


if __name__ == '__main__':
    ThreadingHTTPServer(('127.0.0.1', PORT), Handler).serve_forever()

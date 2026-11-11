"""Drives the REAL Hermes tool pipeline (model_tools.handle_function_call) in a disposable HERMES_HOME.

Nothing the model could ask for is executed: the registry's dispatch is replaced by a canary that only records the call.
A recorded canary call therefore means "the pipeline would have run the tool"; no canary call means it was stopped first.
Human answers go through the same channel the WebUI uses (register_gateway_notify + resolve_gateway_approval).

usage: python runner.py <scenario.json>   (env: ODY_HOME, ODY_AGENT, optional ODY_INJECT, plus anything the case needs)
prints one JSON document on the last line.
"""
import json
import os
import sys
import threading
import time
import traceback

SCENARIOS = json.load(open(sys.argv[1], encoding='utf-8'))
HOME = os.environ['ODY_HOME']
os.environ['HERMES_HOME'] = HOME
sys.path.insert(0, os.environ['ODY_AGENT'])
for extra in filter(None, os.environ.get('ODY_SITE', '').split(os.pathsep)):
    sys.path.append(extra)

if os.environ.get('ODY_INJECT') == 'fs_shape_broken':
    # Hermes discovers plugins while model_tools is imported, so the agent must already look "changed" by then.
    from agent import file_safety as _fs
    _fs._classify_write_denial = lambda path: None                    # an upstream refactor that drops the `entry` parameter

import model_tools                                               # noqa: E402
from tools import approval as ap                                 # noqa: E402
from tools.approval_context import set_current_session_key       # noqa: E402
from tools.registry import registry                              # noqa: E402

CANARY = []


def canary_dispatch(name, args, **kwargs):
    CANARY.append({'tool': name, 'args': args})
    return json.dumps({'canary': True, 'tool': name})


registry.dispatch = canary_dispatch

inject = os.environ.get('ODY_INJECT', '')
if inject == 'fs_shape_broken':
    from hermes_cli import plugins as _plugin_api
    _plugin_api.discover_plugins()
elif inject:
    from hermes_cli import plugins as plugin_api
    plugin_api.discover_plugins()
    module = next((m for name, m in sys.modules.items() if name.endswith('odysseus_ask_policy') or name.endswith('odysseus-ask-policy')), None)
    policy_module = sys.modules.get(module.__name__ + '.policy') if module else None
    if inject == 'decide_raises':
        policy_module.decide = lambda *a, **k: (_ for _ in ()).throw(RuntimeError('injected failure'))
    elif inject == 'decide_sleeps':
        policy_module.decide = lambda *a, **k: time.sleep(6)

results = []
for number, scenario in enumerate(SCENARIOS):
    del CANARY[:]
    cards = []
    session = f'ody-case-{number}'
    human = scenario.get('human', 'absent')
    env = dict(scenario.get('env', {}))
    saved = {k: os.environ.get(k) for k in env}
    os.environ.update(env)
    for key in scenario.get('unset', []):
        os.environ.pop(key, None)
    for hook in scenario.get('before', []):
        if hook == 'write_policy_block_yolo':
            open(os.path.join(HOME, 'odysseus-ask-policy.json'), 'w').write(json.dumps({'yolo_policy': 'block'}))
        elif hook == 'write_policy_gate_files':
            open(os.path.join(HOME, 'odysseus-ask-policy.json'), 'w').write(json.dumps({'gate_file_tools': True}))
        elif hook == 'write_policy_extend':
            open(os.path.join(HOME, 'odysseus-ask-policy.json'), 'w').write(json.dumps({'allow_commands': ['npm test'], 'allow_tools': ['kanban_create']}))
        elif hook == 'write_policy_invalid':
            open(os.path.join(HOME, 'odysseus-ask-policy.json'), 'w').write('{not json')
        elif hook == 'clear_policy':
            try:
                os.remove(os.path.join(HOME, 'odysseus-ask-policy.json'))
            except FileNotFoundError:
                pass
    if human in ('deny', 'once'):
        os.environ['HERMES_EXEC_ASK'] = '1'

        def notify(data, _session=session, _choice=human):
            cards.append({k: (v if isinstance(v, (str, int, float, bool, list)) or v is None else str(v)) for k, v in data.items()})
            threading.Thread(target=lambda: ap.resolve_gateway_approval(_session, _choice), daemon=True).start()
        ap.register_gateway_notify(session, notify)
    else:
        os.environ.pop('HERMES_EXEC_ASK', None)
    if scenario.get('session_yolo'):
        ap.enable_session_yolo(session)
    outcome = {}

    def work():
        token = set_current_session_key(session)
        try:
            if scenario.get('hook_only'):
                # The agent loop (agent/tool_executor.py _pre_tool_block) calls exactly this function before it dispatches any tool,
                # including the ones handle_function_call refuses to run itself (delegate_task, memory, todo_list, session_search).
                from hermes_cli import plugins as _pa
                block, _args = _pa._dispatch_pre_tool_call_hooks(scenario['tool'], scenario.get('args', {}), session_id=session, task_id='t', tool_call_id=f'call-{number}')
                outcome['result'] = json.dumps({'hook_block': block})
                if block is None:
                    CANARY.append({'tool': scenario['tool'], 'args': scenario.get('args', {})})
                return
            outcome['result'] = model_tools.handle_function_call(
                scenario['tool'], scenario.get('args', {}), task_id='t', session_id=session, tool_call_id=f'call-{number}',
                skip_pre_tool_call_hook=bool(scenario.get('skip_hook')))
        except BaseException as exc:                         # noqa: BLE001
            outcome['error'] = f'{type(exc).__name__}: {exc}'
            outcome['trace'] = traceback.format_exc()[-600:]

    started = time.monotonic()
    worker = threading.Thread(target=work, daemon=True)
    worker.start()
    worker.join(40)
    elapsed = round(time.monotonic() - started, 2)
    ap.unregister_gateway_notify(session)
    if scenario.get('session_yolo'):
        ap.disable_session_yolo(session)
    for key, value in saved.items():
        if value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = value
    results.append({'id': scenario['id'], 'tool': scenario['tool'], 'human': human, 'seconds': elapsed, 'timed_out': worker.is_alive(),
                    'executed': [c['tool'] for c in CANARY], 'cards': cards, 'result': str(outcome.get('result', ''))[:700],
                    'error': outcome.get('error'), 'trace': outcome.get('trace')})

print('\n@@RESULTS@@' + json.dumps(results))

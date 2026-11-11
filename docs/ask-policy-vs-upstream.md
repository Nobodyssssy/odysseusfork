# Ask-policy versus the official Hermes Desktop/Gateway protections

Measured 2026-11-11 in an isolated lab: the **official Webapp** from the pinned agent checkout (`3b42157858`, built from `apps/desktop`), a throwaway Hermes home, a scripted stub model, the gateway running at Low integrity, driven through the real `/api/ws` JSON-RPC that the Webapp itself uses. Results are in `docs/evidence/desktop-lab/`; harness in `tests/desktop-lab/`.

Labels: **[lab]** measured through the Webapp protocol, **[lab-direct]** measured against the Webapp's REST routes, **[code]** read from source, **[unit]** the 23 plugin pipeline tests, **[unverified]**.

Lab configuration: `approvals.mode: manual`, `approvals.timeout: 8` (test-only), `HERMES_WRITE_SAFE_ROOT` = a synthetic project folder **plus the Hermes home** (the "wide" case we have in production today).

## 1. What upstream already enforces (reuse as is)

| Protection | Evidence |
|---|---|
| File-tool writes outside `HERMES_WRITE_SAFE_ROOT` refused | [lab] S1 denied |
| Agent file tools may not write Hermes state: `state.db`, `sessions/`, `mcp-tokens/`, `pairing/`, `vault/`, `browser-profile/`, `webapp/` (the Webapp credential) | [lab] S4 denied; list [code] `agent/file_safety.py` |
| `config.yaml`: "Agent cannot modify security-sensitive configuration" | [lab, earlier API run] write_file refused |
| Credential reads refused: `.env`, `auth.json`, `webapp/*`, `vault/*`, project `.env` files anywhere | [code]; [lab-direct] `/api/fs/read-text` refused `.env` and `auth.json` |
| The runtime's own venv is never agent-writable | [code] `runtime_self_protection` |
| Dangerous terminal patterns ask the human in `manual` mode (`powershell -Command ...` was asked) | [lab] S5 raised `approval.request` |
| Unattended surfaces default to deny (`cron_mode`, `unattended_mode`, `single_query_mode`) | [code] defaults; [lab, API run] `execute_code` BLOCKED on api_server |
| Approval UI with once / session / always / deny and a timeout | [lab] event `approval.request`, `choices: once, deny, session, always`, `timeout_s` |
| Webapp authentication: every route 401 without the session token; WebSocket 403 without it; one-use launch link | [lab] T1, 14/14 REST routes 401; link signed the browser in |
| Host-header check (DNS rebinding) and WebSocket Origin check | [lab] evil Host -> 400; evil Origin / rebinding Host+Origin on all six WebSocket routes -> 403 |
| `approvals.deny` globs and `command_allowlist` | [code] |
| `pre_tool_call` plugin hook that can `block` or `approve` any tool through the same approval card | [code] and used by our plugin [lab] |

Upstream's own comment on its file guard: *"Defense-in-depth - not a security boundary; the terminal tool can still bypass."*

## 2. What upstream does NOT enforce, measured (what the plugin adds)

| Scenario (agent tool, official Webapp) | Upstream only | With ask-policy plugin |
|---|---|---|
| S2 write onto a user-protected folder inside the root (`secrets\`) | passes the guard | **denied** (never-allow list) |
| S3 write `plugins\evil\plugin.yaml` when the Hermes home is in the root (a plugin runs code at next start) | passes the guard | **denied** |
| S6 `git push` | **not asked** | asked; deny blocks in ~2 s |
| S7 `execute_code` writing outside the safe root | **ran and wrote the file** | asked; deny blocks, approve runs |
| S9 `delegate_task` (sub-agent) | **dispatched, not asked** | asked; deny blocks, approve dispatches |
| S10 read-only `git status` | not asked | not asked (read-only allowlist) |
| S5 `powershell -Command ... Set-Content` | asked (upstream pattern) | asked **twice**: our prompt, then upstream's |
| S8 MCP tool writing outside the safe root | [unverified] the stub MCP tool was not exposed to the Webapp session | [unit] asked in the real tool pipeline |

Decisions through the real protocol (deterministic, scripted client, 8 s test timeout): deny -> `BLOCKED: Action denied by user` in about 2 s; once -> the action proceeds (S7 wrote the file, S9 dispatched). The prompt text the Webapp shows: `[Odysseus ask-policy] terminal: git ... push origin HEAD | Commands are NOT sandboxed: approving lets this run with your full user rights.`

## 3. Decisions

| Piece | Decision | Why |
|---|---|---|
| Our dialog, Trusted Workspaces UI, `_SESSION_GRANTS`, prompts JS (old WebUI) | **Drop** with the old WebUI | The Webapp has its own approval card with once/session/always/deny. |
| Safe-root, protected state, credential reads, config.yaml protection | **Reuse upstream** | Already enforced; do not duplicate. |
| Ask for tools upstream does not gate (`execute_code`, sub-agents, `git push`, MCP) through `pre_tool_call` + `request_tool_approval` | **Keep** (official extension point, no source change) | Upstream runs these unasked. |
| Never-allow list for user folders and `plugins/` | **Keep, small** | No upstream equivalent. It rebinds a private function (`agent.file_safety._classify_write_denial`), guarded by a self-test that fails closed and a visible status file. Preferred long term: an upstream PR that adds `plugins/` and `hooks/` to `_HERMES_PROTECTED_SUBPATHS`. |
| Store of the never-allow list | **Change** | It reads `vault\webui-trusted-workspaces.json`, a file owned by the retired WebUI patch. Move to a neutral file. |
| Double prompt when upstream already asks (S5) | **Fix** | Skip our ask for a command upstream's detector will already gate when `approvals.mode: manual`. Not done yet. |
| `always` / `session` choices on our prompts | **Review** | The card offers them; for exact-command rule keys they are narrow, but `always` persists. Decide whether to hide `always`. |
| Cron / unattended handling in the plugin | **Keep as defense in depth** | Upstream defaults already deny. |

## 4. What no plugin can fix (needs OS isolation or a source change)

[lab-direct] A signed-in Webapp client can use the REST routes, which do not consult the agent guard:

- `POST /api/fs/write-text`, `POST /api/files/mkdir`, `DELETE /api/files` (recursive) succeeded **outside the safe root, on a never-allow path, onto `config.yaml` (setting `approvals.mode: off`) and into `plugins/`**.
- `GET /api/fs/read-text` of `webapp/session-token` **returned the Webapp credential**.
- `POST /api/git/review/commit` committed with no approval (push failed only because bash cannot start in the Low-integrity lab).
- `POST /api/mcp/servers` accepted `cmd.exe`, `powershell`, `python -c` as an MCP server command, and `.../test` **executed it**.

These are by design for the human user, but they mean anything holding the session token (any process running as you, including the agent's shell, which can read `webapp\session-token`) has full control. The token file is protected from the agent's file tools, not from its shell.

Isolation that works without administrator rights in this lab: a Low-integrity process **does** enforce a write boundary (a Medium-labelled canary was untouched; only Low-labelled folders were writable) but **does not** stop reads or loopback network access, and **MSYS/Git bash does not start at Low integrity**, so a sandboxed gateway cannot run shell tools. A write-restricted token ran `cmd.exe` but not bash. A separate Windows account or a container remains the real boundary for shell-capable agents.

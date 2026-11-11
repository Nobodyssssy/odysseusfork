# Isolated official Webapp prototype: essential security tests and results

2026-11-11. Production was not changed: listeners 7000/8100/8787, `config.yaml`, WebUI settings and sessions, Hermes and Odysseus `.env`, `component.json`, the agent checkout (clean at the pin), the WebUI checkout (8 patch entries), the Odysseus HEAD and the production plugins folder were compared with a baseline taken before the lab started: all identical.

## The prototype

- **Official Webapp built from the pinned agent checkout** (`3b42157858`): `node apps/desktop/scripts/build-webapp.mjs`, into a private workspace under the lab. It needs **Node >= 26**; the Hermes-bundled Node 26.12.1 and pnpm 12.15.0 worked, the system Node 24 does not. The first attempt failed on that engine check and was rerun with the bundled Node.
- **Gateway** from the same checkout, throwaway `HERMES_HOME`, Webapp on `127.0.0.1:19120` (loopback only), API server on 18642, scripted stub model on 18700. Started with `python -m gateway.run`, no `hermes` CLI.
- **Isolation**: the gateway runs as a Low-integrity process (`tests/desktop-lab/lowil_run.py`, kill-on-close job object). No administrator rights, no Docker, no unsandboxed shell.
- **Signed in through a one-use launch link** minted over the gateway's private control channel; a real browser then showed the official Desktop UI (Sessions, Bots, Capabilities, Messaging, Artifacts, Scheduled jobs). The test scenarios appear there as sessions.

## The reduced essential suite (about 1 minute of runtime, deterministic)

| Group | Script | What it proves | Result |
|---|---|---|---|
| Authentication, Host/Origin, WebSockets | `t1_webapp_security.py` | 87 checks | 66 pass, 3 fail, 18 info (below) |
| Direct routes versus the agent guard | `t2_direct_routes.py` | fs / git / MCP routes | 14 of 17 bypass or leak (below) |
| Agent tools through the real `/api/ws` | `t4_webapp_essentials.py` | 10 scenarios x {upstream only, plugin on} + decisions | table in `ask-policy-vs-upstream.md` |
| Approval decisions | same, `:once` / `:deny` | deterministic answers via `approval.respond` | deny blocks in ~2 s; once proceeds |
| Plugin pipeline | `integrations/hermes-mode/plugins/odysseus-ask-policy/tests` | 23 tests, real `handle_function_call`, MCP, cron, sub-agents | pass (earlier run) |
| Rollback | lab flipped `plugin_on` / `plugin_off` repeatedly; production untouched | behaviour reverted each time | OK |

Dropped as old-WebUI specific or not essential: the old WebUI API-server scenario run (it waited the 300 s default approval timeout), prompt/dialog tests, and per-route CSRF matrices for the old WebUI.

Test-only settings: `approvals.timeout: 8`, scripted answers from the client, a stub model that emits one tool call per scenario. The first matrix run (`T4-plugin_off/on.json`) recorded `asked_n = 0` for every scenario because the harness did not yet recognise the Webapp's `approval.request` event; the decisive evidence in those files is the tool result text and the event types, and the later `T4-decisions` / `T4-approve` runs record `asked = 1` with real replies.

## T1: authentication, Host/Origin, WebSockets [lab]

Pass:
- Every tested REST route returns 401 without the session token (14 routes, GET/POST/PUT/DELETE, including file write, delete, Git commit/push, MCP, terminal backend); a wrong token also returns 401.
- With a valid token: Host `127.0.0.1:19120` and `localhost:19120` return 200.
- Host `evil.test:19120`, `127.0.0.1.evil.test:19120`, `evil.test` return **400** (rebinding blocked, also with a valid token).
- `hermes.localhost:19120` and `hermes.localhost:7000` return **400**: the fused address is rejected until `webapp.public_url` is configured (not yet tested).
- CORS preflight from a hostile origin gets no `Access-Control-Allow-Origin`.
- WebSocket on `/api/ws`, `/api/events`, `/api/pub`, `/api/pty`, `/api/host-terminal`, `/api/console`: refused (403) with no token, with a hostile Origin, and with a rebinding Host+Origin. `Origin: http://hermes.localhost:7000` is refused too.

Fail / notes:
1. **`Origin: null` plus a valid token is accepted** on `/api/ws`, `/api/pty` and `/api/console` (opened and sent frames). Needs the token, so low severity, but it is a gap against "trust only the bound loopback origin". Odysseus' proxy can reject `Origin: null`; an upstream fix is also reasonable.
2. A request with an **empty Host header** and a valid token returned 200.
3. A valid token in the query string is accepted on WebSockets; tokens in URLs can reach logs. [code-level concern, not measured further]

## T2: direct routes versus the agent guard [lab-direct]

A valid-token client succeeded at: writing, creating folders and recursively deleting **outside the safe root, on a never-allow path, onto `config.yaml` (including `approvals.mode: off`) and into `plugins/`**; reading `webapp/session-token`; Git commit with no approval; adding an MCP server whose command is `cmd.exe`, `powershell` or `python -c`, and spawning it through `.../test` (a marker file was written). Refused: reads of `.env` and `auth.json` (403). Git push could not be verified (bash does not start at Low integrity). See `ask-policy-vs-upstream.md` section 4 for what this means.

## Not verified

- MCP tool calls through the Webapp (the stub MCP tool was not exposed to the session; covered only by plugin unit tests).
- `webapp.public_url` with the Odysseus proxy, including WebSocket rebinding through `hermes.localhost`.
- Shell tools actually executing (impossible at Low integrity; decisions were checked at the approval layer).
- Behaviour with a real model, real workspaces, or production credentials (deliberately not used).
- The native Electron app (Webapp first, as decided).
- Windows terminal behaviour of the Webapp (`/api/pty` is POSIX-only in this checkout; `/api/host-terminal` returned 403 for the same-origin token case).

# Hermes Desktop migration inventory (read-only)

Prepared 2026-11-11. Nothing in production was changed to produce it. Labels: **[code]** read from source on disk, **[live]** measured, **[docs]** official documentation, **[unverified]** not checked. The Webapp is off on this PC, so nothing below about its runtime behaviour is live-tested.

## 1. Current component versions [live/code]

| Component | Version |
|---|---|
| Odysseus fork | branch `fix/windows-cookbook-delete` / `release/hermes-mode-windows` at `8b428243` (origin); Phase 1 branch local at `219b1160`+ |
| Hermes Agent | pinned `3b421578588243f920886e6fe79a6faa87ea026e`, commit date 2026-11-09 (a few days after release v0.23.0, "The Hearth Release", 2026-11-05 [docs]) |
| Old WebUI | `nesquena/hermes-webui` pinned `f328d3f8e17f…` (`exp-v0.52.653`) + reviewed patch (1,630 lines, 8 files), checkout `F:\hermes-webui`, **adopted/user-owned** (`origin: external`) |
| Hermes Python env | `…\installs\562e58e2ea9aca80\environments\6eff8cc60cb848909fbabec0b09c88c2`, Python 3.14 |
| Desktop (in the pinned agent repo) | `apps/desktop`, Electron 44.9.3 |
| Webapp listener | `gateway/webapp_listener.py`, config `webapp.enabled=false`, `host=127.0.0.1`, `port=9120`, `public_url=""` |

**The official Desktop and Webapp are already in the pinned agent checkout** (`apps/desktop`, `apps/shared`, `gateway/webapp_listener.py`, `hermes_cli/web_routers/*`). The migration is mainly enabling and integrating what is there, not importing a second project. "Fork" is then a fork of `hermes-agent`' Desktop app or an overlay of Odysseus' own plugin/extension on top. This needs your decision (section 8).

## 2. Official Desktop/Webapp features available upstream [code + docs]

Chat with canonical session protocol; profiles; sessions with pop-out windows; Capabilities (skills/tools/MCP); Messaging; Artifacts; scheduled jobs; model/subagent settings; host file tree and editor; **Git review** (status, diff scopes, stage/revert, commit, push, create PR via `gh`, worktrees); previews; terminal rail; Desktop plugins (install from a tab, pinned to a commit); notifications; group chats; desktop auto-update feed; Webapp (same workspace in a browser tab served by the gateway).

Webapp auth [docs/code]: a one-use launch link (valid 60 s) is exchanged for a session token kept per tab; the credential is the dashboard session token stored in a user-only file `webapp\session-token` in the Hermes home [code: `webapp_listener.py`]. Non-loopback bind or `public_url` requires dashboard sign-in (password or OAuth/OIDC). Docs state: a signed-in Webapp user can run arbitrary commands as the OS account running Hermes.

## 3. Custom modifications to preserve

| Piece | Where | Survives the swap? |
|---|---|---|
| Hermes password gate and proxy cookie fix | old WebUI auth + `src/hermes_proxy.py` | Proxy code survives. The password is replaced by the gateway's own sign-in. |
| Fused `hermes.localhost:7000` proxy | `src/hermes_proxy.py` | Survives; must add WebSocket support and gateway Host/Origin trust (section 5). |
| Services menu (restart/stop Hermes, restart Odysseus) | `src/service_controls.py`, `src/application_modes.py`, admin JS | Survives. The active-run probe reads the old WebUI `/health` and must be re-pointed. |
| Component manager: pins, adoption, `plan_existing`, `maintain`, `refresh`, `serve` | `src/hermes_component*.py`, `config/hermes-component.lock.json`, `scripts/hermes-component.py` | Survives; the WebUI pin and patch concepts change to a Desktop/Webapp pin. |
| Return-to-Odysseus button and mode CSS | `return-to-odysseus.js` (102 lines), `modes.css`, extension package `odysseus-mode` | Old-WebUI extension format. Port to a Desktop plugin or a small Odysseus-side shell link. |
| Trusted Workspaces dialog, allow/once/chat/always, never-allow list | WebUI patch (`trusted_workspaces.py` ~ most of the 1,630 lines) | UI is obsolete. The never-allow storage and the agent-side enforcement move to the plugin (section 6). |
| Console-popup fix | `api/windows_console.py` (old WebUI) | Re-evaluate: the gateway/Desktop spawn paths differ. Check for popups after enabling. |
| Provider sharing / sync | `src/provider_sync.py`, `provider_adapters.py` | Survives as Odysseus code; the Hermes-side read must be re-implemented against the gateway API (KI-002). |
| Full backup / restore | `src/hermes_backup.py`, `scripts/odysseus-backup.py` | Survives. Add `webapp\` state and Desktop plugin folders to the manifest. |
| Ask-policy plugin and `filesafety` rebind | `integrations/hermes-mode/plugins/odysseus-ask-policy` | Agent plugin: survives untouched. |
| Auth invariant test | `test_webui_auth_invariant.py` | Principle survives; rewrite against the gateway's routers and its sign-in gate. |
| Claude subscription provider plugin | `hermes\plugins\claude-subscription-directsdk-experimental` | Agent-level, survives. |

## 4. Direct Desktop operations versus the intended security model [code; no live test]

The agent's approval and write-guard layers sit inside the agent loop. The Desktop adds user-driven endpoints beside it:

| Endpoint family | Finding | Consequence |
|---|---|---|
| `POST /api/fs/write-text`, `DELETE /api/files`, uploads, mkdir | `files.py` does not reference `file_safety`, `get_write_denied_error` or `HERMES_WRITE_SAFE_ROOT` (grep; the only `file_safety` hits are comments about a read blocklist). Path checks use the Desktop's own `_resolve_managed_path`/policy [code]. | A signed-in Desktop/Webapp user can write and delete files the agent's guard would refuse. By design (it is the user), but `HERMES_WRITE_SAFE_ROOT` and the never-allow list do **not** protect those files from this path. [unverified: whether the managed-path policy limits roots in this config] |
| `POST /api/git/review/commit`, `/push`, stage, revert, pr | No guard reference. | Git operations run as you, with your credentials, without approval prompts. |
| `/api/pty` | `PtyBridge` is POSIX-only in this checkout; native Windows needs ConPTY [code: `pty_bridge.py` header]. The native Electron app has its own `terminal-ipc`/`node-pty` terminal [code]. | On Windows the terminal rail comes from Electron, not the gateway. Whether the Webapp has a terminal on Windows is [unverified]. |
| Webapp sign-in | A signed-in tab acts as you, with full tool access [docs]. The token file `webapp\session-token` is readable by same-user code. | Same limit as today: code running as you can use the gateway. |

**Protections that need OS-level isolation** (no plugin can supply them): stopping the agent's shell from reading `webapp\session-token`, `.sessions.json`, `.env` and keys; stopping any approved command from touching files outside the safe roots; stopping the user-facing editor/Git/terminal from being used by a compromised agent process. Candidates: run the agent under a separate Windows account or a sandbox/container; deny that account read access to the Hermes secrets folder; keep the Desktop client on your own account.

## 5. Authentication and WebSocket changes required [code]

- **WebSockets**: chat, PTY and the canonical protocol use WebSockets. The Odysseus proxy currently **closes every WebSocket with code 1008** [code: `hermes_proxy.py:91-93`]. It must proxy WebSockets, keeping loopback-peer, no-forwarded-header and exact-Host checks.
- **Host/Origin**: the gateway accepts only loopback Host values plus the exact `public_url` host, and trusts only its own bound loopback port for CORS and WebSocket `Origin` [code: `web_server_boundary.py`; docs "trust only their own bound loopback port"]. Behind `hermes.localhost:7000` the browser's Origin is not the bound port, so we need `webapp.public_url: http://hermes.localhost:7000` (which also engages dashboard sign-in) and the proxy forwarding that Host. [unverified end to end]
- **Credential**: replace the Hermes password with the gateway sign-in (password provider or OIDC) or the one-use launch link. This probably supersedes most of P2-SSO. [unverified how `public_url` + one-use link interact]
- **Cookies**: keep the stateless proxy; the gateway sets its own cookie. Re-run the cookie replay test after the swap.
- **CLI/ACP**: unchanged token path; do not touch.

## 6. What can move to agent plugins or Desktop extensions

- Agent plugin (already built): ask-by-default policy, never-allow enforcement, status file. Move the never-allow store to a neutral file.
- Desktop plugin (official system): return-to-Odysseus button, mode label, permission-state indicator ("commands are not sandboxed", approval mode, YOLO).
- Odysseus-side code: proxy, Services, backups, component manager, provider sync, health probe adapter.

## 7. Required security tests and rollback

Tests before any live Desktop enablement, all on a disposable Hermes home first: cookie-less requests to every route refused with sign-in on; rebinding Host/Origin rejected on HTTP and WebSocket; one-use launch link replay/expiry; token file permissions; ask-policy on file tools, shell, MCP, sub-agents, cron, `execute_code`; direct fs/Git endpoints behave as in section 4; proxy streaming/upload/WebSocket; backup, restore into an isolated copy; Services restart with a live run, including the post-reply window.

Rollback: keep the old WebUI checkout, pin and patch unchanged until the Desktop passes; switch back by setting the component to the old WebUI and disabling `webapp.enabled`; restore `config.yaml` from `F:\Odysseus-Backups\config-backups`; restore data from the verified snapshot. Do not delete the old WebUI until a rollback has been rehearsed.

## 8. Decisions needed from you

1. **What is "the fork"?** (a) Run the official Desktop/Webapp from the existing agent checkout with Odysseus plugins/extension, or (b) fork `hermes-agent`' `apps/desktop` to carry changes. (a) keeps Hermes updates cheap; (b) is only needed if a feature cannot be a plugin.
2. **Webapp (browser, behind the Odysseus proxy) or native Electron Desktop?** Your current setup is a browser tab at `hermes.localhost:7000`, which points to the Webapp.
3. Host allowlist: patch the old WebUI or rely on the password until the swap (recommended)?

## 9. Missing Claude Code / Codex features worth adding later

Not verified against the Desktop; candidates only: per-command approval with "always for this exact command prefix"; session-scoped permission modes shown in the UI; plan mode; a visible diff before accepting each file edit; checkpoints/rewind; hooks; project rules file; sandboxed command execution; per-tool allow/deny rules in project settings.

## 10. Decisions recorded (user, 2026-11-11) and what the lab changed

- Fork strategy: **Option A first** (official Desktop/Webapp from the pinned agent checkout, Odysseus integration via plugins, extensions and APIs). Long term, a fork of the **whole** `NousResearch/hermes-agent` repository, created only when source-level customization is needed, with a clean upstream relationship.
- **Browser Webapp first, native Electron second.** Final product: browser at `localhost:7000` with Personal (Odysseus) and Projects (Hermes), a unified Windows desktop app with the same two modes and the same backend data, Linux afterwards. Do not discard Electron.
- Host/Origin: no more patches to the old WebUI; complete the reusable **Odysseus-side** Host/Origin protections including WebSocket origin validation; document the remaining direct-8787 rebinding risk; the password is temporary defense in depth. The new gateway/Webapp must pass HTTP and WebSocket rebinding tests before production activation.
- Disposable tests only; no production changes, no live plugin install, no restarts, no push, no deploy without separate approval.

## 11. Prototype findings that change the plan (see `desktop-lab-results.md`, `ask-policy-vs-upstream.md`)

1. **The official Webapp builds and runs in isolation from the pinned checkout** (Node >= 26 and pnpm from the Hermes tools folder) and signs in through a one-use launch link.
2. **The Odysseus proxy cannot carry the Webapp today.** The Odysseus venv has neither `websockets` nor `wsproto`, so a WebSocket upgrade to `hermes.localhost:7000` is answered `400 Invalid HTTP request received` by uvicorn (measured live, handshake only, no credentials) and the proxy's own WebSocket branch is unreachable. Needed: a WebSocket library, a relay that preserves Host/Origin handling, and the same loopback-peer / no-forwarded-header / exact-Host rules; reject `Origin: null`.
3. **`webapp.public_url` is required for the fused address**: the gateway rejects `hermes.localhost:<port>` (HTTP 400, WebSocket 403) otherwise. Setting it also engages the dashboard sign-in gate. Not yet tested.
4. **Upstream protects more than assumed** (Hermes state, credentials, config, venv) and **less than assumed** for `plugins/`, `execute_code`, sub-agents, `git push` and MCP.
5. **Direct Webapp routes bypass the agent guard** (fs write/delete, Git, MCP server commands); only OS-level isolation or a source change addresses that.
6. Odysseus pieces reused unmodified: component manager and pins (add a Desktop/Webapp build step), Services controls (re-point the active-run probe), full backup (add `webapp\` state and Desktop plugin folders), proxy cookie fix, ask-policy plugin (with the changes in `ask-policy-vs-upstream.md` section 3).

## 12. Which features need our own Hermes fork (not a plugin or extension)

Candidates, in order of how likely they are to need a fork rather than an upstream PR:

- Guard the direct fs/Git/MCP routes with the write guard and the never-allow list (source change in `hermes_cli/web_routers/*`).
- Add `plugins/` and `hooks/` to `_HERMES_PROTECTED_SUBPATHS` (tiny; upstream PR first).
- Reject `Origin: null` and empty Host on gateway WebSockets/HTTP (tiny; upstream PR first).
- A supported hook for path vetoes, replacing our rebind of a private function.
- Odysseus Personal/Projects mode switching inside the Desktop shell (a Desktop plugin if the plugin API allows; otherwise a fork).
- Claude Code / Codex features (section 9 of this document): plan mode, checkpoints/rewind, per-command prefix rules, project instruction files and review UI need a feature-by-feature comparison against the Desktop first; much of it may already exist upstream (Git review pane, worktrees, checkpoints).

## 13. Proposed migration sequence

0. **Done:** isolated Webapp prototype and essential tests (no production change).
1. Odysseus-side code on `feature/phase1-permissions`: WebSocket-capable proxy (add `websockets`), Host/Origin/`Origin: null` rules, unit tests plus the lab matrix. No production change.
2. Lab: a second Odysseus instance on spare ports in front of the lab gateway with `webapp.public_url` set; run T1 through it, including HTTP and WebSocket rebinding via `hermes.localhost`.
3. Plugin changes: neutral never-allow store seeded with `plugins\`, `hooks\`, Odysseus `.env` and `data\`; remove double prompts; decide on the `always` choice; extend tests (MCP through the Webapp).
4. Component manager: pin and build the Webapp from the agent checkout (Node 26 + pnpm from the Hermes tools folder); launcher owns `webapp.enabled`, `webapp.host: 127.0.0.1`, `webapp.public_url`, `approvals.mode: manual` and `HERMES_WRITE_SAFE_ROOT` for the Hermes child.
5. **Needs your approval:** production parallel run, Webapp enabled on loopback 9120 beside the old WebUI, ask-policy plugin installed, approvals set to manual, no proxy cutover.
6. **Needs your approval:** proxy cutover behind a flag (`hermes.localhost:7000` to 9120), old WebUI still running unproxied.
7. Retire the old WebUI after a stable period; keep its pin and patch in history.

## 14. Rollback

- Odysseus: flip the proxy upstream flag back to 8787; `webapp.enabled: false`.
- Hermes: restore `config.yaml` from `F:\Odysseus-Backups\config-backups\` (a backup exists from before the allowlist cleanup); disable the plugin by removing `odysseus-ask-policy` from `plugins.enabled` (reverts to upstream behaviour: shown repeatedly in the lab).
- Data: the verified snapshot `F:\Odysseus-Backups\snapshot-20261111-130449-db3827` and `scripts/odysseus-backup.py restore` (dry run first; an isolated real restore is still to be rehearsed).
- The old WebUI checkout, pin and reviewed patch stay untouched until the rollback has been rehearsed in the lab.

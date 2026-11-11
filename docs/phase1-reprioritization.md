# Phase 1 reprioritization (Hermes Desktop migration decision)

Decision (user, 2026-11-11): the patched Hermes WebUI is replaced by a fork of the official Hermes Desktop/Webapp. Odysseus stays primary. Stop expanding the old frontend. Security first, but no effort on an interface being replaced.

Labels: **[code]** read from source, **[live]** measured on this PC, **[sim]** disposable test, **[unverified]** not checked.
Gates unchanged: no production gateway change, no plugin install, no approval-mode change, no restart, no push, no deploy without explicit authorization.

## 1. Classification of the open backlog

Categories: **1** required security/data integrity, **2** required for current production stability, **3** must be adapted for the official Desktop, **4** obsolete old-WebUI-specific work.

| Item | What it was | Category | Decision |
|---|---|---|---|
| P0-1 Default deny for managed installs | Odysseus launcher sets `HERMES_WRITE_SAFE_ROOT` for the Hermes child only; an empty list must never mean unrestricted (the agent treats empty as unrestricted, `file_safety.py:303`) | **1**, survives migration (launcher is Odysseus code) | Do now, merged into "narrow `HERMES_WRITE_SAFE_ROOT`" below. The Windows user variable is your setting: I propose the value, you set it. |
| P0-4 Real scopes | One-operation grant, per-run grant, chat grants bound to chat lifetime, "until restart" labelled honestly | **4** for the dialog and `_SESSION_GRANTS` code in the old patch; **3** for the requirement | Cancel in the old WebUI. Carry the scope model into the migration plan as a requirement on the Desktop approval flow. |
| P0-5 Show the folder actually granted | Show file versus folder, flag `.git`, `.env`, `node_modules`, default to the narrowest parent | **4** (UI) / **3** (requirement) | Cancel in the old WebUI. Requirement for the Desktop approval card. |
| P0-6 Per-chat attribution | Prompts only in the chat that asked; refusal memory keyed by (chat, folder); `_LAST_POLL` is global | **4** / **3** | Cancel in the old WebUI. The Desktop's own approval events are per session [code: `input-requests.ts`]; verify when migrating. |
| P0-7 Persist Deny | Remember denies across restarts | **4** / **3** | Cancel in the old WebUI. Never-allow already persists (below). |
| P0-8 Safe Restart/Stop | Services block while a run is active; close the ~1 s window after a reply while the session persists | **2**, partly **3** | Do now. Active runs come from the old WebUI's public `/health` `active_runs` [code: `application_modes.py:143`]. Put the probe behind one adapter so the Desktop gateway can replace it. Do not gate `/health` until the adapter exists, or restart protection breaks. |
| P0-9 Docs and real test counts | Remove stale claims, record real counts | **2** | Do now. `docs/hermes-mode.md` line 3 still says the branch "does not include" Trusted Workspaces [live]. Fix it; record 23 plugin tests, 6 invariant tests, 10 plan tests, 12 proxy tests. |
| Task 4 Host allowlist | Reject unknown `Host` before routing | **1** for Odysseus' proxy; **4** for patching the old WebUI | See section 3. |
| Provider sync token (KI-002) | Scoped read-only token for `/api/providers` | **3** | Plan only. The Desktop gateway has its own models API. |
| P2-SSO | One login across Odysseus and Hermes | **3**, P2 | Spec only. See the inventory for the official Webapp's one-use launch link, which may replace most of it. |

## 2. Finish before migration (in this order)

1. **Narrow `HERMES_WRITE_SAFE_ROOT` and protect security files** (category 1).
   - [live, measured earlier] With the current 7 roots the agent's file tools can write the Hermes password hash, session store, WebUI auth code, component config, Odysseus source/`.env`/`app.db`. Narrower roots fix this; I propose one value, you set it.
   - The plugin's never-allow list is read from `vault\webui-trusted-workspaces.json` [code: `filesafety.py:33`], a file owned by the old WebUI patch. Move it to a neutral file (`vault\odysseus-protected-paths.json`) so it survives the swap, and seed it with: Hermes `.env`/`auth.json`/`config.yaml`, WebUI/Desktop session and token files (`webapp\session-token`), `.signing_key`, `plugins\`, Odysseus `.env`, `data\`, and the component config.
   - No agent code path can be made to protect files from the agent's own shell. That needs OS isolation (section 4).
2. **Ask-policy plugin on a disposable Hermes** (already passes 23 tests against the real pipeline). Next: a disposable full-gateway run, including cron and sub-agents, then your authorization before any live install.
3. **Verify permission enforcement across every execution path** (file tools, shell, MCP, sub-agents, cron, execute_code, ACP, text-to-speech). Existing tests cover file tools, shell, MCP, sub-agents, cron and the three unguarded write predicates. Still to test: ACP end to end, `execute_code`'s own file writes, and tool results that start processes.
4. **Direct Desktop operations** (section 4 of the inventory): read-only findings done; live tests need a disposable Desktop gateway.
5. **Host allowlist and rebinding** (section 3 below).
6. **P0-8 and P0-9.**
7. **Backups**: full backup and dry-run restore exist. Still to do: an isolated real restore into a copy, because only the dry run has been exercised.
8. **Preserve** (do not regress): proxy cookie fix, `plan_existing` fix, the invariant test (its logic is WebUI-specific; the principle carries over), command-allowlist cleanup (done by you).

## 3. Host allowlist: what survives the migration

- Old WebUI direct `:8787` with a forged `Host`: only the Hermes password stops it today [live]. The official gateway already ships a Host-header check (`host_header_middleware`, DNS-rebinding advisory GHSA-ppp5-vxwm-4cf7) with a loopback allow-set plus the exact `public_url` host [code: `web_server.py:385`, `web_server_boundary.py`]. So patching the user-owned old WebUI is category 4. **Recommendation: do not patch it; rely on the password until the swap.** Say so if you want the patch anyway.
- The Odysseus proxy (`src/hermes_proxy.py`) only forwards requests whose `Host` is exactly `hermes.localhost:<port>`; other hosts go to Odysseus, which has its own auth [live: 401]. Odysseus-side hardening that survives migration: add a WebSocket `Origin` check and an `Origin`/`Host` consistency rule for state-changing methods before forwarding, driven from config. Worth doing now.

## 4. Do not build in the old WebUI

Design changes, session management, Git UI, provider displays, command palette, scope/prompt improvements. Reuse the official Desktop source.

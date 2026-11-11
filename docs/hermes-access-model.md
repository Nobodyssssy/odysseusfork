# Hermes access model: what the password does and does not protect

Last verified 2026-11-11 against the pinned WebUI (f328d3f8e) plus the reviewed patch.

## What is closed
- **The web.** A page on the internet (including a DNS-rebinding page) cannot read Hermes sessions or start chats, terminals or approvals: every state-changing and data route returns 401 without a Hermes login, directly on `127.0.0.1:8787` and through `hermes.localhost:<odysseus port>`.
- **Other users of this PC / other devices.** They have no Hermes session, and the Odysseus proxy only accepts loopback peers without forwarding headers.
- The Odysseus proxy no longer stores or replays Hermes cookies (`src/hermes_proxy.py`), so one login cannot be reused by another local client.

## What is NOT closed
- **Code running as you.** The Hermes session store `.sessions.json` (and the signing key) live in the Hermes state folder, readable by any process running under your Windows account, including the agent's own shell. Such code can obtain a live session and call `127.0.0.1:8787` directly. The password does not defend against it.
- The only real fixes are a separate Windows account for the agent or the Phase 3 execution boundary. Nothing in the UI or docs may imply otherwise.
- Commands run by the agent are **not sandboxed**. Approval prompts are a request to a human, not a boundary. The pattern-based "dangerous command" detector misses ordinary writes, pushes and uploads, and your `command_allowlist` entries bypass it entirely.
- Sessions last 30 days by default; a longer lifetime widens the window in which a stolen session file is useful.
- `/health` is public by design and shows run counts and model names.

## Invariant (tested)
The browser origin check passes requests that carry no Origin/Referer/Sec-Fetch-Site headers on purpose, so curl, MCP and the CLI keep working. Therefore Hermes authentication must always stay on; `integrations/hermes-mode/patches/test_webui_auth_invariant.py` fails if any route outside a reviewed public list becomes reachable without credentials.

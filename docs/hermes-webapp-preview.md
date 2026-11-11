# Odysseus + official Hermes Webapp: isolated preview

2026-11-11, branch `feature/webapp-proxy`. Disposable lab only: nothing here was installed, enabled, restarted or pushed in production.
Labels: **live** = measured in the preview with a real browser or real sockets; **unit** = automated test; **code** = read from source;
**unverified** = not measured.

## 1. What it shows

One external port (127.0.0.1:19800 in the preview, 7000 in production):

- `http://localhost:19800/` is the **real Odysseus application** (Personal).
- `http://hermes.localhost:19800/` is the **official Hermes Desktop Webapp**, served by a separate gateway process on 127.0.0.1:19120 and reached
  only through the Odysseus proxy. No iframe on either page (0 iframes on both, live).
- Odysseus' mode selector reads **Personal | Projects**. Choosing Projects asks Odysseus for a one-use sign-in link and navigates the tab to it.
- Inside the Webapp, a **← Personal** item in its status bar returns to Odysseus. It is a standard runtime **Desktop plugin**
  (`desktop-plugins/odysseus-return/plugin.js`) loaded by the Webapp's own plugin loader; the Webapp itself is unmodified.

## 2. How it is built

| Piece | File | Notes |
|---|---|---|
| Proxy, `gateway` mode | `src/hermes_proxy.py` | WebSocket relay, exact Host/Origin policy, loopback peer, no forwarding headers, log redaction; see `hermes-webapp-proxy.md` |
| Backend `gateway` | `src/application_modes.py` | `ODYSSEUS_HERMES_BACKEND=gateway`; status via the Webapp's public `/api/health`; Stop/Restart refused (the gateway is not owned by Odysseus) |
| Launch route | `POST /api/application-modes/projects/launch` | authenticated, same-origin, local administrator only; returns `{url}` with `Cache-Control: no-store`, `Referrer-Policy: no-referrer` |
| Launch helper | `scripts/hermes-webapp-launch.py` | run with the Hermes interpreter; asks the gateway's private control socket (same OS user only) for the link; prints it to stdout only |
| UI | `static/js/application-modes.js` | labels Personal/Projects, calls the launch route, validates the returned address before navigating |
| Return control | `integrations/hermes-mode/desktop-plugins/odysseus-return/plugin.js` | copy into `<hermes home>/desktop-plugins/odysseus-return/` |

The one-use link keeps its ticket in the URL **fragment** (never sent to a server, never in a Referer); the Webapp strips it after redemption
(live: fragment is `/` afterwards). The route never logs it, stores it or puts it in an error message (unit: 7 tests).

Two gateway configurations were tested; they need different proxy settings:

| | Ungated | Sign-in gate on |
|---|---|---|
| Gateway | `webapp.public_url` empty | `webapp.public_url: http://hermes.localhost:<Odysseus port>` + `dashboard.basic_auth` |
| Proxy `ODYSSEUS_HERMES_PROXY_UPSTREAM_HOST` | `bound` (default): Host/Origin rewritten to the gateway's own | `public`: browser's Host/Origin/Referer pass through unchanged |
| Why | gateway accepts only its own loopback authority | gateway insists on the real origin for cookie writes (live: rewritten Origin returned 403 "Cookie-authenticated writes must come from the Webapp's own origin") |
| Credential | launch link signs the tab in; token lives in that tab's `sessionStorage` | the launch link does **not** bypass the gate: the tab lands on the official Sign-in page; login sets HttpOnly SameSite=Lax cookies |

## 3. How to run the preview

```
python tests/desktop-lab/preview.py setup     # synthetic admin + session, fresh state dir, marker .env in the worktree
python tests/desktop-lab/preview.py start     # PREVIEW_ENV_ODYSSEUS_HERMES_PROXY_UPSTREAM_HOST=public for the gated variant
python tests/desktop-lab/lab.py start         # the isolated gateway (see tests/desktop-lab/README.md)
python tests/desktop-lab/gate_config.py on    # optional: sign-in gate (restart the lab gateway afterwards)
python tests/desktop-lab/preview.py clean     # stop, delete state, remove the marker .env
```

Isolation of the preview process: own data dir, database, `auth.json`, `sessions.json`; minimal environment (no inherited `ODYSSEUS_*` or
`HERMES_*` variables; `HOME`/`APPDATA` redirected); a marker `.env` in the worktree so python-dotenv cannot find `F:\Odysseus\.env`; lifespan
jobs, schedulers, mail pollers, warm-ups and keep-alive off; loopback bind; ports 19800/19120 (never 7000/8100/8787/9120). Checked live: no
connection from the preview to a production port, `F:\Odysseus\.env` hash unchanged, production listeners kept their PIDs.

## 4. Results

### 4.1 Personal ↔ Projects in the real Odysseus app: works (live)

Personal → Projects → Personal → Projects, twice, both modes (ungated and gated). The Odysseus login survived every round trip. The Personal
side loaded (`/api/health`, `/api/auth/status`, `/api/application-modes`, `/api/sessions` all 200; Notes, Tasks, Library, Calendar open without errors).

### 4.2 Complete official Webapp through the proxy: yes (live)

Loaded with its normal UI and settings. Works: sign-in, sessions list and switching (two sessions, no cross-talk), chat, **incremental streaming**
(token index 38 → 44 between samples), **Stop** (UI stopped at tok44; the model side logged `client-disconnected` after 47 of 60 chunks),
tool calls (upstream write guard denial shown in the thread), approval cards (**Reject** blocked the command, no file; **Run** executed once,
file written only in the synthetic lab folder; an unanswered card timed out to BLOCKED), Capabilities (skill catalog), Messaging (platform list),
Artifacts, Scheduled jobs (dialog, blueprints), Settings (Model, Chat, Appearance, Workspace, Safety, Browser, Passwords & Logins, Memory,
Voice, Advanced, Notifications, Billing, Providers, Gateways, Keyboard Shortcuts, Tools & Keys, Sessions, Plugins, About), Profiles menu,
Layout editor, Files/CWD panel, Bots mode (default "Hermes" bot), runtime plugin loading (← Personal), `GET /api/git/status` from the UI.

Did not exercise: Git review pane (needs a project folder; the UI's `git/status` call returned 200 through the proxy), the Terminal panel
(renders; it dialed no socket and I did not open a shell), voice/audio sockets, Messaging platform setup (would be outbound), native Electron-only features.
Quirks: the Backend chip stays "checking" while the tab is not visible and focused (upstream gates readiness refresh on focus; it reads
"ready" once focused); in the ungated mode a fresh tab without a link shows an empty shell, not a sign-in prompt (the gate fixes that: `/` redirects to `/login`);
after a Hermes sign-out the Webapp says "Run hermes webapp on this computer to sign this browser in" (upstream wording for a CLI-launched Webapp).

### 4.3 Authentication and session continuity (live unless stated)

- Launch route: no session cookie 401; wrong cookie 401; no Origin 403; foreign/`null`/`hermes.localhost` Origin 403; `X-Forwarded-For` 403;
  bearer header 403; forged Host 403; GET 405; legitimate admin 200 with `no-store` and `no-referrer`.
- Webapp REST: no credential 401 on every route tried (profiles, sessions, config, fs read, git status); wrong bearer 401.
- WebSockets (valid one-use ticket, ungated): 9 hostile upgrades (evil Origin, `null`, none, other-port Hermes origin, **Odysseus origin**, cross-site fetch
  metadata, `X-Forwarded-For`, forged Host, evil Host+Origin) all 403 and **did not consume the ticket**; the legitimate upgrade then 101 with
  `hermes-gateway-v1`; replay, forged and absent tickets 403. Unit tests cover the same matrix against a fake gateway.
- Reload keeps the Webapp signed in (same tab). A new tab has no credential (all `/api/*` 401).
- **Odysseus logout** (superseded by the scoped logout gate, `hermes-webapp-grants.md`): in this first preview it revoked only the Odysseus session and left an open Webapp
  session working. With access grants on (the default in gateway mode) it now ends that browser session's access to Hermes through the proxy immediately, including open
  sockets; it still cannot revoke a copied Hermes token or protect direct gateway access.
- **Hermes sign-out** (`webapp-sign-out`, the control the CLI uses): closed 3 live sockets, old token 401, UI shows "Sign in to Hermes"; launching
  again from Odysseus restored the session (history intact).
- Sign-in gate (`gate-probe.json`, 41 pass / 0 fail / 9 informational): unauthenticated `/` redirects to `/login`; wrong password and wrong user 401;
  correct password from an evil/`null`/cross-site origin 403; cookies HttpOnly, SameSite=Lax, host-only; 7 wrong passwords then 429 for the window;
  ticket minting and WebSocket upgrade work through the proxy only with the pass-through Host/Origin; hostile Origin/Host/forwarding headers refused;
  replay refused; **`POST /auth/logout` only clears the browser's cookies (302 to `/login`): the live socket stayed open and a copied cookie still
  returned 200** (stateless tokens: upstream source says "nothing to revoke server-side"); **`webapp-sign-out` closed the socket (code 4401) and made
  the copied cookie and ticket minting return 401**. Direct to the gateway with valid cookies, `Host: evil.test` and `127.0.0.1.evil.test` return 400.
- A gateway restart ended the gated browser login (observed once; the old cookies stopped working).
- Credentials in logs: none (the Webapp token, Odysseus cookie and lab password were searched for in the Odysseus and gateway logs). httpx and
  uvicorn log request URLs at INFO, so gateway mode now redacts `token=`, `ticket=`, `access_token=`, `session_token=`, `code=`, `launch=` (unit: 3 tests).

### 4.4 Original HermesGUI customizations still to migrate

| Customization | Status |
|---|---|
| Return-to-Odysseus button / mode CSS | **Done** as a Desktop plugin (needs install into the Hermes home by the component manager) |
| Mode selector, Services menu | **Done** for Personal/Projects; Services Stop/Restart for the gateway not implemented (hidden); Restart Odysseus unchanged |
| Hermes password gate + cookie fix | Superseded by the gateway's own sign-in; the stateless-proxy cookie fix is kept |
| Trusted Workspaces dialog, grants, never-allow UI | Obsolete (Webapp approval card). Never-allow *store* and enforcement stay in the ask-policy plugin; store still to move to a neutral file |
| Provider sharing/sync (Hermes side) | **Not migrated**: hidden in Projects mode (KI-002); re-implement against the gateway API |
| Console-popup suppression (`windows_console.py`) | Not re-checked; verify the gateway's spawn paths on a real install |
| Component manager (pins, adoption, `serve`, `maintain`) | Still pins the old WebUI; needs a Webapp build step and gateway ownership |
| Backups | Add `webapp\` state, `desktop-plugins\` and the gate secret to the manifest |
| Active-run probe for restart safety | Still reads the old WebUI `/health` |
| Ask-policy plugin | Unchanged, tested live through the Webapp (reject/approve/timeout) |

### 4.5 Remaining production blockers

1. Direct Webapp routes (fs write/delete, Git, MCP server commands) bypass the agent guard (earlier lab finding, unchanged). Needs OS isolation or a source change.
2. Same-user code can read the Webapp credential (`webapp\session-token`, or the gated login's secrets) and act as you.
3. Scoped logout is implemented and tested in the disposable preview (`hermes-webapp-grants.md`); it is not global sign-out and cannot revoke copied Hermes credentials or protect the gateway's own port. The grant database must join the backup set.
4. Gated mode means a second (password) login; ungated mode means a bearer token in `sessionStorage`. Choose; a single login needs the P2-SSO design.
5. The component manager cannot build, pin, start or back up the Webapp yet; Services controls do not cover the gateway.
6. Install `websockets` in the production Odysseus venv first (gateway mode answers 501 to every WebSocket without it; fails closed).
7. Install the ask-policy plugin and set approvals to manual (separate approvals), and narrow `HERMES_WRITE_SAFE_ROOT`.
8. The gateway's "Automatic Repository Discovery" is on by default and scans the gateway user's home when no roots are set; set roots explicitly.
9. Provider sync, popup re-check and a Windows terminal check are unverified.

## 5. Reversible production migration (each step needs your approval)

1. Install `websockets` into the Odysseus venv (additive; inert). Rollback: uninstall.
2. Deploy this branch with defaults (`webui` backend and proxy mode): behaviour unchanged. Rollback: previous commit.
3. Build the Webapp from the pinned agent checkout; enable it on loopback beside the old WebUI (`webapp.enabled`, port 9120, `public_url` set for the gated variant, `dashboard.basic_auth` with a generated password stored only by you).
4. Install `desktop-plugins/odysseus-return` and the ask-policy plugin; approvals manual.
5. Set `ODYSSEUS_HERMES_BACKEND=gateway`, `_MODE_URL`, `_GATEWAY_HOME/_PYTHON/_AGENT_DIR`, `ODYSSEUS_HERMES_PROXY_MODE=gateway` (and `_UPSTREAM_HOST=public` if gated); restart Odysseus.
6. Rollback at any step: clear `ODYSSEUS_HERMES_BACKEND` and `ODYSSEUS_HERMES_PROXY_MODE` (or `ODYSSEUS_HERMES_PROXY=0`), restart Odysseus, `webapp.enabled: false`; restore `config.yaml` from `F:\Odysseus-Backups\config-backups\`; the old WebUI is untouched throughout.

## 6. Regression and cleanup

See the final report for the numbers; the commands are `pytest tests -n 4 --ignore=tests/smoke --ignore=<two fcntl-only modules>` in a venv built from `requirements.txt`.

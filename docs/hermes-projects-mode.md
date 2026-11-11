# Personal / Projects: Odysseus with the official Hermes Webapp

Branch `feature/webapp-proxy`, local only (nothing pushed or deployed). Evidence: `docs/evidence/projects/` (16 screenshots, disposable preview, real browser, real managed install).

## What it is

- **Personal** = Odysseus. **Projects** = the official Hermes Desktop Webapp, served at `hermes.localhost:<port>` through the Odysseus port by the proxy (no iframe).
- One selector in the Odysseus bar switches. In Projects, the Webapp's status bar carries "← Personal".
- `ODYSSEUS_HERMES_BACKEND=webapp` selects the **managed component** (installed by `scripts/hermes-webapp-component.py ensure`). Odysseus starts that gateway on demand and may stop it. An **adopted** gateway (your own install) is only talked to: Odysseus never starts, stops or restarts it.
- Not installed, or disabled: the selector does not appear and Personal is unchanged.

## Behaviour (all measured in the preview)

| Requirement | Result |
|---|---|
| Projects checks gateway status; starts it if stopped | cold start from the click to the Webapp in about 42 s; with Hermes already running a switch takes 11 to 14 s; a Services restart took about 24 s; the bar shows "Checking Hermes…", then "Starting Hermes…" |
| Reuse a running gateway | switching with Hermes ready did not touch it (no start/stop calls; unit-tested) |
| States | `ready`, `starting`, `stopped`, `error` (with the gateway's own message), `conflict` (another program on the port: nothing touched), `unavailable`; "busy" is shown as ready plus a count |
| Failure handling | a failed start is shown, and the next click retries (a bug found by the tests and fixed: the first version blocked retries after an error) |
| Never take over an unrelated or adopted process | refused in `ModeService` and again in the component (pid record must match the process Odysseus provisioned) |
| Never restart because of a mode switch | confirmed live: about six Personal ↔ Projects switches with Hermes running, and the gateway log shows only two starts for the whole session (the on-demand start and my own Restart click) |
| Return to Personal does not stop runs | confirmed live, see below |
| Services menu preserved | Start / Restart / Stop Hermes (managed only), Restart Odysseus unchanged; Stop with sessions active asks twice |

### Continuity across a switch (what survives)
Switching is a full page navigation between two origins, so the Webapp page itself is reloaded, but nothing the gateway keeps is lost:

- open conversations and their history: kept (server side)
- project folder and repo state: kept
- **unsent draft in the composer: kept** (the Webapp restores it)
- Webapp settings: kept
- sign-in: no prompt (a new one-use launch link is exchanged; the grant cookie is reused)
- **an agent run in progress: not stopped, not lost.** Leaving mid-stream and returning either re-attaches to the live stream or, if it finished meanwhile, shows the complete saved answer (60 of 60 tokens in both cases)

Not preserved: the exact scroll position and the open right-hand panels of the Webapp. The cost of the reload is about 7 to 11 s per switch. Avoiding it needs the Webapp to run in its own tab (decision for you, below).

An earlier run that seemed to lose output was a bug in my test stub (it never ended its SSE stream), not the gateway; it is fixed in the lab tools.

## Files, editor, Git (official UI, guarded gateway)
- Project file tree, source/diff viewer, **Edit then Save** (wrote to disk through the guarded route), Review pane, stage + commit (commit `7e4e0c0` created, including the editor's change): all work.
- **The editor "bug" was not a bug**: a single click opens a viewer (Source / Diff); the **Edit** button makes it editable. The Git review list needs a double click to open the file view.
- Publishing (push / PR) is off by default (`HERMES_WEBAPP_ALLOW_GIT_PUBLISH=1` to opt in). A commit outside `HERMES_WRITE_SAFE_ROOT` is refused with a clear message.
- **Windows folder picker `..`**: confirmed upstream bug in `remote-picker.tsx` (`parentDir` only knows `/`; on `F:\a\b` it cut one character, and the breadcrumbs invented `/F:/…`). Fixed in the route-guard patch (`remote-picker-paths.ts`, 4 unit tests run under Node). **Not re-tested live**: I could not find a control that opens the picker for a session that already has a project; the earlier live reproduction is the only UI evidence.

## Blocked-folder list (no longer depends on the old WebUI)
- Neutral store: `<gateway home>/vault/odysseus-protected-paths.json` (`{"version":1,"blocked":[{"path":…}]}`), inside the already protected `vault` folder, so neither the agent nor the web routes can write it (live: 403).
- The route guard and the ask-policy plugin read the neutral file first and the retired WebUI file `webui-trusted-workspaces.json` only if the neutral one does not exist. The first write through the component imports the legacy entries.
- Manage it: `scripts/hermes-webapp-component.py blocked [--block PATH] [--unblock PATH] --root <component root>`. An unreadable list makes the guard refuse writes (fails closed).

## Unrestricted direct execution surfaces (the gateway is not sandboxed)
These remain, by design of the official Webapp, and must not be described as protected:

1. **Terminal pane / PTY**: a shell as your Windows user, no approval, no path guard.
2. **Agent tools** once approved or when approval mode allows: shell, code execution, file writes within `HERMES_WRITE_SAFE_ROOT`.
3. **MCP servers**: the guard blocks `cmd`, `powershell`, `bash`, `python -c` style one-liners, but `npx`, `uvx` and `python script.py` servers are arbitrary code.
4. **Direct gateway port** (`127.0.0.1:<gateway port>`): reachable by any local process that holds the Webapp token; Odysseus logout cannot revoke it.
5. **Same-user code** can read the Webapp credential file. Only a separate Windows account or sandbox closes this (`hermes-windows-isolation.md`).
6. SSH remote file backend, MCP catalog install, `chat/image-upload`: not covered by the route guard.

## Local preview (disposable; touches nothing in production)
From `F:\Odysseus-Backups\desktop-lab` with `unset PYTHONPATH HERMES_HOME`:

1. Install the managed component into a lab root (about 12 minutes: clone of the pin, patch, pnpm, build):
   `ody-full-venv\Scripts\python.exe scripts\hermes-webapp-component.py ensure --root <lab>\smoke-root --origin managed --python <hermes venv python> --home <lab>\smoke-root\state\hermes-gateway --port 19130` (run from the Odysseus worktree)
2. Give the lab gateway a model: add the stub model block to `smoke-root\state\hermes-gateway\config.yaml` and run `python tools\stub_llm.py 18700` (a fresh home otherwise shows the official provider onboarding).
3. `export PREVIEW_MANAGED_ROOT='<lab>\smoke-root' PREVIEW_ENV_HERMES_WRITE_SAFE_ROOT='<lab>\smoke-root\work\safe'`, then `python tools\preview.py setup`, `python tools\preview.py start` (Odysseus only: no gateway is started).
4. Open `http://localhost:19800/` with the synthetic admin from `odysseus-preview\lab-admin.secret`; choose Projects.
5. Stop: Services > Stop Hermes, then `python tools\preview.py stop`, `python tools\preview.py clean`, stop the stub.

## Remaining UI problems
- Each switch reloads the other app (7 to 11 s); scroll position and open panels are not kept.
- The "active" count is the gateway's "sessions touched in the last five minutes", so Stop can ask for confirmation when nothing is actually running (the wording now says so).
- The first start after install takes about 42 s with no progress detail beyond "Starting Hermes…".
- A fresh managed home has no model, so Projects opens the official onboarding; providers must be shared or configured first (provider sync is separate).
- The picker fix needs a live re-test.

## Remaining production-migration blockers
1. `websockets` must be installed in the production Odysseus venv (otherwise gateway mode answers 501 to every WebSocket).
2. Install the managed component for real (clone, build ~12 min) and decide the port; set `ODYSSEUS_HERMES_BACKEND=webapp` (production restart of Odysseus: needs your word).
3. Provider and model sync into the new gateway home, and copying your Hermes Desktop settings/skills/memory into it (or adopting the existing home instead of managing a new one).
4. `HERMES_WRITE_SAFE_ROOT` narrowed to your real project folders (your action), and the project folders on Btrfs must be confirmed to honour the guard (not measured).
5. The ask-policy plugin is not installed live; the same neutral blocked list applies once it is.
6. The grant database and gateway home are in the backup set; a full restore drill has not been run on the new component.
7. Windows isolation (separate account) is a design only.

## Recommended reversible migration
1. **Backup**: run the full backup (Odysseus data, Hermes home, component config); note the restore command.
2. **Install in parallel**: `ensure --origin managed` on a free port and a new home; nothing existing changes. Check `status` shows `route_guard: applied`.
3. **Prove it off to the side**: start Odysseus with the new backend on a spare port (as the preview does), against the new home with the stub model, then with your real provider.
4. **Seed**: provider sync, blocked-folder list (`blocked --block …`), `HERMES_WRITE_SAFE_ROOT`.
5. **Switch**: with your word, set `ODYSSEUS_HERMES_BACKEND=webapp` and restart Odysseus via the scheduled task. The old Hermes WebUI on 8787 stays installed and running.
6. **Rollback**: remove the setting and restart Odysseus (Projects disappears or returns to the old WebUI mode); `hermes-webapp-component.py disable` keeps the install; `rollback` returns to the previous release; the WebUI component and its backups are untouched.

## Decision for you
Keep same-tab switching (simple, one tab, each switch reloads), or open Projects in its own tab (no reload, the Webapp keeps its socket, panels and scroll, at the price of two tabs and an opener relationship that needs `noopener` care)? Same-tab is what is implemented.

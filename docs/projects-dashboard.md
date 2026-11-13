# Projects dashboard (first increment)

Branch `feature/projects-dashboard` (worktree `integration-backups/projects-dashboard`), local only: not pushed, not deployed.
Page `/static/projects.html` (link "Dashboard" in the Personal bar), API `/api/projects-dashboard` (loopback browser administrator only, read-only).

## What the references do, and what we already had

| | Orca (MIT) | T3 Code (MIT) | Vibe Kanban (Apache-2.0, sunset) | AionUi (Apache-2.0) | Ours before |
|---|---|---|---|---|---|
| Model | Repo (`kind: git\|folder`, base ref, worktree base path) -> Worktree ("workspace") with its own status | Project -> Thread; sidebar rows with a status pill, linked PR and worktree path | Project -> Task -> Workspace (one worktree) -> Session (one executor) -> ExecutionProcess | Many agent CLIs behind one ACP layer | Four separate registries: WebUI `projects.json`/`workspaces.json`, Webapp `projects.db`, write grants, Wiki Steward projects |
| Agent state | Hook-fed `working/blocked/waiting/done` plus a verdict (`failed/interrupted/unconfirmed`); worktree rollup priority permission > failed > working > monitoring > unconfirmed > interrupted > done > inactive. An end it cannot prove is "unconfirmed", not "done" | Thread status pill from the orchestration read model | `Running/Completed/Failed/Killed` per process | Process registry | Per-conversation records existed but nothing joined them |
| Review | Diff/review per worktree, source-control AI | Source-control pane, PR badges | Review + approve a workspace | - | Git pane inside the Webapp (not a cross-project view) |
| Tasks | Workspace status columns (a board over worktrees) | - | Kanban board is the product | - | Wiki Steward tasks and handoffs (the single source of truth) |

Adopted: Repo -> worktrees grouping (via the Git common dir, so `F:\Odysseus` shows its ten worktrees as one project); evidence-first state with "unconfirmed/unknown" instead of guessing; priority-ordered attention; compact sidebar rows with status dot, branch and counts. Not adopted: a new task board or database (Wiki Steward and Hermes `kanban.db` already exist, currently empty), a new agent manager, a framework. No reference code was copied.

## Data sources (all existing, all read-only)
WebUI `projects.json`, `workspaces.json`, `sessions/_index.json`; Hermes `projects.db`, `state.db` (`sessions`, `session_admissions`, `session_turn_leases`, `gateway_heartbeats`); `managed/webapp/component.json` (write roots); `vault/odysseus-protected-paths.json`; `HERMES_WRITE_SAFE_ROOT`; Wiki Steward `tasks/` and `handoffs/` files; the repositories themselves.

## State rules (`session_state`, `task_state`)
Working: a started admission with a live gateway heartbeat and unexpired lease, or gateway activity within 90 s, or a WebUI stream flag with a running WebUI and an update within 15 min. Blocked: an admission in `unknown` (lost turn, queue paused until `/discard`), or a WebUI `recommended_recovery_action`. Interrupted: started turn without a live gateway/lease, reaped session, stale stream flag, unprocessed pending message. Completed: last turn completed and the session closed. Idle: open session whose last turn finished. Unknown: no record. Waiting-for-user is supported by the schema but never emitted: approval prompts are held in gateway memory and are not recorded anywhere readable.

## Permissions shown
Webapp gateway grant (per worktree), legacy `HERMES_WRITE_SAFE_ROOT`, blocked folders, exact request/revoke commands (copy only; the UI never changes grants). "Allow for this chat" is session-scoped in memory and cannot be listed. The page states that file-tool rules are not OS containment.

## Known limits
- Conversations are tied to a project by workspace folder only; Hermes sessions started from the home folder appear under "All conversations".
- Resume links open the legacy WebUI (`localhost:8787/session/<id>`) or the Webapp (`hermes.localhost:<port>/<id>`); the Webapp answers 401 until "Open Projects" has signed the browser in (button in the page).
- Git status runs on selection and manual refresh only (never on the 20 s overview poll).
- The registered project folder is shown as the project path; the Git common directory only groups worktrees.

## Remaining, in priority order
1. Applying grants from the page (one confirmation, through the component's validated `roots` command) and showing the worktree Git-dir warning.
2. A Webapp plugin chip (workspace, branch, changes, write allowed) fed by this API.
3. Wiki "decide/accept" review actions for the reviewer (read-only today), and back-up button.
4. Per-project pins, per-project default model (stored Odysseus-side).
5. Claude Code / Codex activity as additional agent sources (hooks to the Wiki Steward already carry task state).
6. Hermes `kanban.db` tasks when populated.
Backlog kept as requested: better `unknown_execution` recovery UI, plugin-install latency, Windows multi-drive folder picker, Claude Desktop Code tab comparison via REA.

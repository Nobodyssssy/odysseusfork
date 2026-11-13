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


## Phase 1.5 results (production readiness, read-only)
Verified against the real Odysseus app (isolated preview, own data dir, real middleware, synthetic fixture), not the stub:
- Auth: anonymous 401 on overview, detail and sessions; forged cookie 401; admin 200; `hermes.localhost` host, proxy headers (`X-Forwarded-For`), bearer token and a guessed internal token all refused; malformed or traversal project ids 404; responses `Cache-Control: no-store`; no credential words in the payload. The preview-only auth bypass lives outside the repository (`preview.py`); `app.py` wires the router with the default admin guard and a test fails if that changes. The static HTML/JS shell is public like every `/static` file and carries no data. Not tested: a second, non-admin user (the gate is `require_admin`, shared with the provider catalog).
- Input: project id must fully match 10 hex characters (a trailing newline used to pass `$`; fixed with `fullmatch`); no caller-supplied path reaches Git or the filesystem; Git runs with `--no-optional-locks`, `core.fsmonitor=false` (a repository config can no longer run a program on status; tested with a hostile repo) and only on selection.
- Conversations: the WebUI index and the gateway `state.db` hold the same migrated conversations, so they are merged by id (26 became 18). A conversation present in the gateway resumes in the Webapp; only WebUI-only ones use `localhost:8787`, shown as "Resume (legacy)" and implemented as a temporary compatibility route.
- Webapp resume: the pinned Webapp keeps its credential per tab and has no deep-link route, so a plain link opens signed out. The page now asks Odysseus for the normal launch ticket with `?resume=<id>` (id validated), opened with the same `referrerPolicy: origin` link pattern the mode selector uses (without it the proxy refuses the exchange because Odysseus sends `no-referrer`). The launch page carries the id into `/?odysseus_resume=<id>` and the new Desktop plugin `odysseus-resume` calls `host.openSession(id)`. Verified in Firefox against the isolated stack: click, sign-in, landing and the plugin's `openSession` call (resolved) all happen. Not verified: opening an existing real conversation (the lab gateway has none). Without the plugin the user lands signed in on the Webapp start page.
- Home-folder conversations: no record ties them to a project, so they stay unplaced. Evidence-only addition: conversations whose recorded agent tool calls referenced files under a project (3 or more calls) are listed in that project's Sessions tab under "Related (inferred)", never counted as the project's own and never given a state they have no record for.
- Reliability: missing or offline folders show "folder not found" and skip Git; read-only operations are tested to leave every file and `.git` directory unchanged; narrow layout (560 px) fixed so nothing overflows; search, filters, `/`, `j`/`k`, `1`-`5` and Wiki panels unchanged.

## Phase 2: grant / revoke from the Access tab (implemented locally, not deployed)
Files: `src/projects_grants.py`, routes `POST /api/projects-dashboard/grants/preview`, `POST .../grants/apply`, `GET .../grants/audit`, Access-tab buttons and a confirmation dialog.
Decisions:
1. No new permission store. The change is made only by the component's own `set_write_roots(add=[x])` / `set_write_roots(remove=[x])`, validated first by its `root_problem` and described by its `inspect_root` (the grants plugin re-reads that list on every file call: no restart). `clear` is never used.
2. Two steps. `preview` is read-only and returns the canonical (real) path, the matched worktree, the current list, warnings and a single-use confirmation (120 s) bound to the action, the canonical path, the signed-in user and a digest of the exact current list. `apply` refuses an expired, reused, other-user or stale-list confirmation.
3. Explicit confirmation: a grant needs the folder's name typed exactly; a revoke (which only narrows access) needs one click. Writes need a same-origin browser administrator (`require_browser_admin`: Origin check, no API token, no internal-tool identity).
4. Narrow by construction: only folders the dashboard shows for that project (registered folders, Git worktrees); only the exact top folder of a Git repository or worktree (never a parent, drive root, profile, credentials, Odysseus or Hermes); never wildcards; an existing grant is never widened or rewritten; revoke removes only an exact entry. Revoking the last grant leaves an empty list, which the gateway treats as deny-by-default (the empty no-workspace folder), not as the operator's variable (tested).
5. Warnings shown before confirming: Git data outside the worktree (commits write there), other worktrees sharing it, Btrfs, a grant that contains existing grants.
6. Audit: append-only JSON lines at `<component root>\managed\webapp\grant-audit.jsonl` (outside every write root, fsynced), recording time, user, action, canonical path, list before/after, warnings, result (applied, refused, failed) and peer address; the last entries are shown in the Access tab.
7. Wording: every dialog and the Access tab state that this is an application rule for the Hermes file tools, not OS containment.
Verified on the isolated real app: guards (anonymous 401, no Origin 403, foreign Origin 403, bad path 422), dialog opens without changing the list, wrong name keeps Confirm disabled, grant and revoke change exactly one entry, audit written.
Not covered: "Allow for this chat" approvals (in gateway memory), legacy `HERMES_WRITE_SAFE_ROOT` (user environment variable, shown read-only), Btrfs write behaviour.

## Deployment notes (when authorized)
Merge the branch into `F:\Odysseus` and restart the Odysseus app (new router in `app.py`); for in-page Resume deep links copy `integrations/hermes-mode/desktop-plugins/odysseus-resume/plugin.js` to `<gateway home>\desktop-plugins\odysseus-resume\plugin.js` (the component installer still copies only `odysseus-return`; changing it would alter its existing tests). Nothing here changes the gateway, the WebUI patch or any grant.

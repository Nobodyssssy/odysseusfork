# Route guard for the Webapp's direct file, Git and MCP routes

Patch: `integrations/hermes-mode/patches/webapp-route-guard.patch` (applies to the pinned agent revision `3b421578`; owned by the Webapp component only (`ROUTE_GUARD_PATCH` in `src/hermes_webapp_component.py`), NOT in the shared `_PATCHES` table, so the WebUI component never touches the live agent checkout. Applied to a checkout that is clean at the pin; a foreign state is refused. A managed gateway refuses to start without it; an adopted (user-owned) gateway runs the user's own unpatched code and reports `route_guard: absent`). Source of truth for the code and its 19 tests is the patch itself (`hermes_cli/web_route_guard.py`, `tests/hermes_cli/test_web_route_guard.py`). **Not deployed.**

## What changed
Before: the direct REST routes did not consult the agent's file-safety rules, so a caller holding the Webapp credential could (measured in the lab, T2) write or delete outside `HERMES_WRITE_SAFE_ROOT`, replace plugin/config files, read the Webapp credential and register a shell as an MCP server.
After: the same verdicts the agent tools get (`agent.file_safety`), plus a short list the agent leaves editable on purpose but a REST call must not touch.

| Route family | Rule |
|---|---|
| `fs/write-text`, `files/upload*`, `files/mkdir` | write verdict: safe root, protected Hermes state, credentials, NT namespace, `plugins/ hooks/ desktop-plugins/ config.yaml cron/` |
| `DELETE /api/files` | same, and also refuses a folder that CONTAINS protected state (recursive delete of the Hermes home) |
| `fs/read-text`, `fs/list`, preview/download, `files/*` reads | read verdict: credentials, `.env`, `webapp/`, tokens |
| Git stage/unstage/revert/commit/worktree/branch | the repository path must pass the write verdict; Git reads must pass the read verdict |
| Git push, create-PR, commit-with-push | disabled unless `HERMES_WEBAPP_ALLOW_GIT_PUBLISH=1` (a REST call cannot tell a human click from a script, and a push uses your stored Git credentials) |
| any route, any folder in `<home>/vault/webui-trusted-workspaces.json` `blocked` | your own deny list also applies to reads, writes and deletes; an unreadable list refuses writes (fails closed) |
| MCP add / replace | refuses a shell or script host as the server command (`cmd powershell bash sh wscript mshta rundll32 ...`), an interpreter given inline code (`python -c`, `node -e`), and shell operators |

## Limits (read before trusting it)
- A policy boundary for the web vector, not a sandbox. Same-user code ignores it (see `hermes-windows-isolation.md`).
- Blocking `cmd` still allows `npx`, `uvx` and `python server.py`: an MCP server is code by design. The check removes the unreviewed one-liner, not the capability.
- Not guarded: the SSH/remote file backend (a different machine), MCP catalog install (curated entries), toggling an already-configured MCP server, `chat/image-upload`, and the terminal/PTY endpoints (these already need the Webapp token and are the user's own terminal).
- Publishing is off by default: the Desktop Git pane's Push / Create PR buttons will return 403 with the setting named until you opt in.
- Pull: the guard blocks reads under `webapp/` but not a process that opens that file directly.

## Evidence
- live, disposable gateway from the patched tree (T2 direct-route script rerun): bypasses 14 of 17 before, 3 of 17 after. The 3 left are accepted by design: recursive delete of a folder INSIDE the safe root (the Files UI's normal function), and an MCP server running `python script.py` plus its test spawn (an MCP server is code by design).
- live, normal use (REST): read/list/edit/new file inside the safe root, git status/stage/commit all 200; push 403 (opt-in), plugins write 403, credential read 403, an `npx` MCP server accepted.
- live, real browser (`docs/evidence/guard/`): Projects mode on the guarded gateway, Git review pane with diff, CWD file tree and terminal pane; stage + commit from the pane created the commit. A commit in a repo OUTSIDE `HERMES_WRITE_SAFE_ROOT` is refused with `Git changes are not allowed here: ... is outside HERMES_WRITE_SAFE_ROOT`: set the variable to include every project you want to commit in.
- Upstream folder-picker bug seen (not caused by the guard): the `..` button trims one character instead of a path segment on a Windows backend (backslash paths). Migration item.

## Earlier note
- unit (real route functions, pinned source, scratch Hermes home): 19 pass with the patch; 18 of 19 fail on the unpatched pin. Not run: the Webapp UI against a patched gateway (the next lab session should confirm the Git pane and file editor still work for a normal project folder).

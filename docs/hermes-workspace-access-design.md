# Workspace access: design on top of the existing approval system (planning only)

Date 2026-11-12. Nothing here is implemented, installed or deployed. Every claim is from reading the pinned agent (`3b421578`, release `427ff712100d`) or one live read of the gateway's environment.

## 0. Finding that changes the picture (live, read-only)

The managed gateway (pid 24200) was started with `HERMES_WRITE_SAFE_ROOT` = `adkar; Sahib-Android; F:\Odysseus; F:\hermes-webui; F:\Odysseus-Backups; %LOCALAPPDATA%\Odysseus\hermes; %LOCALAPPDATA%\hermes\webui`. Cause: when the component's own `write_roots` list is empty, `hermes_webapp_component.py` (line ~948) passes through the **operator's user-level variable**, the one the old WebUI uses. So "write roots empty" has **not** meant "no writes". The gateway can write inside two of your separate projects and inside the Odysseus install and its backups. It also means the roots are not empty in the sense the cutover docs and my reports claimed. Hermes's own credential and protected-path denials still apply on top.

Also: in the agent, an empty/unset variable means **no root restriction at all** (`if safe_roots and not any(...)`), so "empty = nothing writable" is not available by setting the variable empty; a deny-all needs a root that contains nothing.

Not changed. Proposed fix: a distinct component default that denies project writes (a root pointing at an empty folder owned by the component), instead of inheriting. Needs your approval and a gateway restart.

## 1. What the pinned Hermes already provides (reuse)

| Layer | Mechanism | Where |
|---|---|---|
| Command approval | modes manual / smart / off, once / session / always / deny cards, `approvals.deny` globs (apply even under off), `command_allowlist` | `tools/approval.py`, `approval_floors.py`; Webapp `approval-mode-menu.tsx`, `approval.tsx` |
| Write approval for special paths | `_run_approval_gate` for `~/.ssh/config` and protected instruction files; honours the same mode; fail-closed with no human | `tools/file_tools_write_guards.py` |
| Write boundary | `HERMES_WRITE_SAFE_ROOT` read **on every call** (`get_safe_write_roots`, `os.getenv`, no cache); credential/protected denylist; NT namespace block | `agent/file_safety.py` |
| Route guard | web file/Git routes call the same classifier | `hermes_cli/web_route_guard.py` (our patch) |
| Never-allow floor | `odysseus-ask-policy` plugin rebinds `_classify_write_denial`, re-reads its policy file by mtime (24 tests, not installed) | `phase1/.../odysseus-ask-policy` |

**Distinction to keep:** command approval asks "may this action run now?" and is per session and per command. Workspace access asks "which folders may the agent change at all?" and is a standing grant by you. An approval card must never widen a workspace grant, and a workspace grant must not skip command approval.

## 2. Gaps that genuinely need development

1. **No runtime store.** The classifier reads the process environment, which only changes on restart. But the read is per call, so one small in-process hook can supply live roots. The same hook mechanism already exists in the ask-policy plugin.
2. **No read-only vs read-write notion.** The agent has write roots only. Reads are not restricted to workspaces (only credential files are blocked). Per-workspace "read" is therefore **not enforceable** today; it can only be shown as "reads are not restricted".
3. **No per-profile or per-project scope.** One root list per gateway process.
4. **The terminal bypasses all of this.** `file_safety` says so itself ("not a security boundary; the terminal tool can still bypass"). Command approval is the only control there, and approved commands run as your user. A workspace grant is therefore a **file-tool and route boundary, not a sandbox**.
5. **No UI** for listing, granting, revoking, or showing the effective permission.

## 3. Proposed design (one source of truth, no second database)

- **One grants file** owned by Odysseus' component: `<component>/state/workspace-grants.json` (the existing `write_roots` config entry becomes this file's only writer; the CLI `roots` command and the UI both write through the same validator). Each entry: absolute folder, mode `rw`, created time, who granted, and the real Git dir and common dir for worktrees. Registering a project writes nothing here.
- **A small plugin in the managed gateway** (`odysseus-workspace-grants`, loaded like `odysseus-return`, not an upstream patch): rebinds `agent.file_safety.get_safe_write_roots` to read the grants file (re-stat per call, cached by mtime), **fail-closed**: unreadable or corrupt file = deny-all; a failed install = the gateway refuses to start the plugin and Odysseus shows "workspace enforcement inactive". Same install self-test pattern as the ask-policy plugin.
- **Revocation takes effect on the next file call** with no restart and no lost session, because the check is per call. A write already in progress finishes; an approved long terminal command is not interrupted (terminal limit above).
- **Validation reuses `set_write_roots`** (exact repo top folder, never `.claude`, `.codex`, `.ssh`, `.aws`, `.gnupg`, `AppData`, the Odysseus install, a drive root). Grant action requires an explicit confirmation in Odysseus (not in the agent's own chat) and is logged.
- **Effective permission shown in Projects** (Webapp plugin, status-bar chip plus project header): `Workspace: write allowed | read-only (no grant)`, `Approvals: manual | smart | off` (read from the existing menu state), and a warning chip when approvals are off in a granted workspace. It reads the grants file through an Odysseus endpoint; it does not keep its own copy.
- **Approval modes unchanged.** In manual and smart mode the existing cards keep working inside granted folders; outside them the file tool is denied before any card appears (a denial, never a prompt, so no card can bypass it).

## 4. Order and risk

1. Decide §0 (approve the deny-by-default root; needs a gateway restart once).
2. Plugin + grants file + tests against the preview gateway (the install self-test, grant, revoke without restart, corrupt file, symlink and junction escape, worktree Git dir, Btrfs paths).
3. Odysseus endpoint and the Projects chip/panel.
4. Reuse the ask-policy never-allow list as a separate, later floor.

Does not need building: approval modes, cards, allowlist editor, deny globs, the registry, the Git UI.


## Implementation status (branch `feature/workspace-grants`; nothing deployed)

Built and tested in isolation:

- `integrations/hermes-mode/plugins/odysseus-workspace-grants`: rebinds `agent.file_safety.get_safe_write_roots` (the one function the agent uses; an empty result there means "no restriction", so "no workspace" is a folder nothing can live under). It reads the component's own `component.json` `write_roots` whenever the file changes, ignores the environment, re-checks each root (absolute, not a drive root, not the profile folder or a parent), fails closed on a missing, corrupt or wrongly shaped file, runs a self-test at install, and writes `odysseus-workspace-grants.status.json` into the gateway home.
- Component: `gateway_environment` no longer inherits the operator's `HERMES_WRITE_SAFE_ROOT`. The environment is always the component's empty `no-workspace` folder and `ODYSSEUS_WORKSPACE_GRANTS` points at `component.json`. The `roots` command changes the same file the plugin reads, so there is one list, one validator, and no second database.
- Tests (`test_workspace_grants.py`, against the pinned agent's real `file_safety`): no grant means nothing writable even with a permissive environment; grant, add, revoke and revoke-all take effect on the next call with no restart; corrupt, wrong-type, wrong-shape and deleted grant files fail closed; drive-root, profile, relative and non-string grants are ignored while credential and Hermes-state denials still win; a junction, `..` and a sibling-prefix folder cannot escape a grant; the status file reports installed and not installed.

Not done (each needs approval): install the plugin into the managed gateway home and enable it in `plugins.enabled` (the component must merge into the list, not replace it); restart the gateway once so it picks up the new environment; the Odysseus endpoint and the Projects chip that show effective permissions; a grant confirmation dialog. Until the plugin is enabled, the new environment alone makes the gateway read-only everywhere except its empty folder.

Known limit: the terminal tool, MCP servers and approved commands are not covered; revoking a grant does not stop a process that is already running.

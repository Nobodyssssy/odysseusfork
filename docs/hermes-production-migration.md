# Production migration: old Hermes WebUI to the official Webapp (runbook, not executed)

Nothing here has been run against production. Every step marked **GATE** needs your explicit approval first. The old Hermes WebUI (port 8787) and its install stay in place until you decide otherwise, so rollback at every step is "remove the setting and restart Odysseus".

## Facts that shape the plan (measured on this PC, read-only)

| Fact | Evidence |
|---|---|
| Production Odysseus venv `F:\Odysseus\venv` has uvicorn but no `websockets` / `wsproto`; production starts `uvicorn app:app --host 127.0.0.1 --port 7000` with no `--ws` option | live check |
| The existing Hermes agent checkout (`...\AppData\Local\hermes\hermes-agent`) is **at the reviewed pin** `3b421578` | `hermes-webapp-component.py plan` against the real home: `checkout: at-pin`, `writes_performed: false`, production files and home listing identical before and after |
| The existing home does not enable the Webapp, has repository discovery on with no roots (it would scan the home folder), approvals mode unset, no `odysseus-return` plugin | same `plan` output |
| An **adopted** (user-owned) install is read-only for Odysseus, so the **route guard patch cannot be applied to it**; only a **managed** install carries the guard and the managed component refuses to start without it | code + tests |
| Managed install takes about 12 minutes (clone, patch, pnpm, build) and runs on its own port and home | measured |

## Decision needed: managed or adopted

| | Managed (recommended) | Adopted |
|---|---|---|
| Route guard (file/Git/MCP) | yes, enforced at start | **no** (your unpatched code) |
| Existing Hermes settings, skills, memory, sessions | start fresh, then copy what you want (see step 4) | all kept |
| Needs edits to your files | none | you edit your `config.yaml` (enable webapp, discovery roots, approvals) |
| Odysseus may start/stop it | yes | never |
| Rollback | `disable` or remove the setting | remove the setting |

Recommendation: managed, because the guard is the reason this migration is safe to do, and copying the settings you care about is a one-off.

## Steps

1. **Backup** (read-only on production): run the full backup and note the restore command and the snapshot name. Confirm `verify` passes. Output goes to `F:\Odysseus-Backups`.
2. **GATE: install `websockets` into `F:\Odysseus\venv`** (a production venv change; pin the version used in the preview) and add `--ws websockets` to the launcher's uvicorn command. Rollback: uninstall and restore the launcher line from the backup. Nothing uses it until the Projects backend is configured.
3. **Install the managed component in parallel** (no production process is touched):
   `python scripts\hermes-webapp-component.py ensure --origin managed --python <Hermes venv python> --home <new gateway home> --port 9130 --root <component root>`; check `status` reports `route_guard: applied`. Port 9130 is free on this PC (9119/9120 are in use).
4. **Seed the new home**: model providers (provider sync), `blocked --block <folders>` for private folders, and copy any skills or memory you want to carry over. Set `HERMES_WRITE_SAFE_ROOT` to your real project folders (your action in the Windows user environment). Confirm Btrfs project folders honour the guard on a throwaway folder first (not measured).
5. **Rehearse off to the side**: start a second Odysseus on a spare port with the same settings as the preview (`ODYSSEUS_HERMES_BACKEND=webapp`), use Projects with the real provider on a scratch repo, then stop it. Production still untouched.
6. **GATE: switch.** Set `ODYSSEUS_HERMES_BACKEND=webapp` and `ODYSSEUS_HERMES_COMPONENT_ROOT` for the production Odysseus and restart it with the scheduled-task procedure (kill launcher tree, then Start-ScheduledTask). No active agent runs may be interrupted. Projects now appears; Hermes starts the first time it is chosen.
7. **Verify** on `localhost:7000`: Personal works, Projects starts Hermes, chat and a Git commit in a scratch repo work, Services menu shows managed controls, logout from Odysseus ends Projects access.
8. **Keep the old WebUI** on 8787 for a trial period. Retire it only on your word.

## Rollback
- Quick: remove `ODYSSEUS_HERMES_BACKEND=webapp`, restart Odysseus (Projects goes back to the old mode or disappears); the managed gateway can be stopped from Services or `hermes-webapp-component.py stop`.
- Component: `disable` keeps the install; `rollback` returns to the previous release.
- Data: restore the step 1 snapshot (Odysseus data, Hermes state); the grant database is intentionally not restored.
- The `websockets` package is harmless to leave installed.

## Still open before the GATEs
- Windows isolation (separate account) is design only; the gateway is not sandboxed. The terminal pane, approved agent tools, `npx`/`uvx`/script MCP servers, the direct gateway port and same-user code remain unrestricted (`hermes-projects-mode.md`).
- A restore drill on the new component has not been run.
- Provider sync into the new home has not been run end to end.
- The ask-policy plugin is not installed in any live home.

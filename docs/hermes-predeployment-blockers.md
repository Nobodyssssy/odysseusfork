# Pre-deployment blockers: status

Production is unchanged and nothing is pushed. Everything below was done in the repository and the disposable lab.

| # | Blocker | State |
|---|---|---|
| 1 | Narrow writable project roots | Mechanism done. **Needs your folder list.** |
| 2 | Seven skills missing from the UI | Explained: not lost, hidden by design. |
| 3 | `ensure` / `repair` | Fixed. |
| 4 | Providers, Claude/Codex subscriptions | Done: isolated by default, deliberate opt-in. |
| 5 | WebSocket dependency | Staged and hash-checked; not installed. |
| 6 | Old WebUI as rollback | Kept; one-line switch back. |
| 7 | Same-user isolation limit | Documented in `hermes-windows-isolation.md`. |

## 1. Writable roots
The managed gateway now has its own list, set with `hermes-webapp-component.py roots --add-root <folder>` (`--remove-root`, `--clear-roots`).
It is passed to the gateway as `HERMES_WRITE_SAFE_ROOT` and does **not** change your Windows user variable or the old WebUI. With no list, the gateway
inherits the operator variable as before. Refused: drive roots, your whole profile folder or a parent of it, Windows/Program Files, the Hermes install,
the gateway home and the Odysseus component folder. Settings survive a rebuilt release. A running gateway needs a restart to pick up a change.
Which folders to list is your decision; none has been added.

## 2. The seven skills (63 of 70 listed)
Checked with the pinned agent's own filter against the migrated copy. Hidden on purpose, by each skill's own frontmatter:

- `platforms: [macos]`: apple-notes, apple-reminders, findmy, imessage
- `platforms: [linux, macos]`: xurl, python-debugpy
- `environments: [kanban]`: sdlc-review. Its platform list includes Windows, but it is only offered inside Kanban task runs, not normal chat.

Nothing is lost or mis-migrated. Not changed: editing those skills would be a local fork of their definitions.

## 3. ensure / repair
- `ensure` on an installed component verifies and heals the cheap parts (missing gateway home or config, return plugin, leftover releases). It never rebuilds, and
  it reports (rather than silently applies) a different `--home` or `--port`.
- `repair` does the same, can move home/port, and rebuilds (about 12 minutes) only if the release no longer verifies. `repair --rebuild` forces it.
- Both print what they did. A killed build no longer leaves an orphaned release: unfinished and unreferenced releases are swept, the newest two failed ones are kept for logs.
- `start` refuses with a clear message if the gateway home is missing.

## 4. Providers and subscriptions
- Provider configuration moves in `config.yaml` and is verified equal (rehearsal: 23/23 sections). Credentials never move; re-enter the one key, `OPENCODE_ZEN_API_KEY`.
- The gateway runs as you, so it can find your Claude (`~/.claude`) and Codex (`~/.codex`) logins. Measured with the pinned agent: unprotected, both paths resolve to your real login
  files; with the component's setting they resolve to empty folders (Claude CLI reports `loggedIn: false` there; your real credential file untouched).
- Default is **isolated**. To use a subscription on purpose: `hermes-webapp-component.py logins --allow claude` (and/or `codex`), then restart the gateway. `--deny` reverts. `status` shows the state.
  An allowed login is readable by anything the agent runs as you, which is the same-user limit in section 7.

## 5. WebSocket dependency
Production venv: Python 3.12.10, uvicorn 0.54.0, **no** `websockets`, so `ws=auto` selects nothing and Projects WebSockets would fail. Staged wheel `websockets-17.4-cp312-cp312-win_amd64.whl`
(sha256 in `config/production-dependencies.json`) in `F:\Odysseus-Backups\wheels\websockets-17.4`. Measured against the production interpreter with the wheel on a scratch path: uvicorn selects
`WebSocketsSansIOProtocol`, production site-packages unchanged. Install is offline (`pip install --no-index --find-links`); undo is `pip uninstall websockets`.

## 6. Rollback
`scripts/hermes-production.py plan` (read-only) lists readiness and the exact ordered procedure. Projects mode is switched on by one line, `ODYSSEUS_HERMES_BACKEND=webapp`, in the production `.env`
(`env-on --approved`, a copy kept). `env-off --approved` removes it and a restart returns to the old behaviour. The old WebUI on :8787 is never stopped or modified by any step.
Current plan result: 0 blockers; to do after approval: code fast-forward (19 commits), websockets install, managed component install.

## 7. Same-user isolation
See `hermes-windows-isolation.md`: the web and other-user routes are closed; code running as your Windows user can still read what you can read. Only a separate account or sandbox closes that.

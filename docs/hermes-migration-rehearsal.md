# Migration rehearsal: your Hermes data in a managed gateway (disposable)

Run 2026-11-12 on spare loopback ports against a **copy** of your real Hermes home. Production (Odysseus 7000, Chroma 8100, old WebUI 8787, your Hermes home, checkout and venv) was not modified: same listener PIDs, same config/.env/auth/SOUL/memory/settings, clean agent checkout, no `websockets` in the prod venv. Raw results: `docs/evidence/rehearsal/results.json`. Screenshots contain your conversation titles and account quota, so they are **not** in the repo (`desktop-lab/results/rehearsal-screenshots/`).

Setup: the verified managed build (release `bc395c178f22`, pin `3b421578`, route guard applied) was reused, no reinstall; a fresh empty managed home was created, then `hermes-migrate.py bundle`, `plan`, `apply`, `verify` were run exactly as the production runbook describes.

## What works (measured)
| Area | Result |
|---|---|
| Migration | 13/13 sessions, 10,685/10,685 messages, 728/728 file hashes; no credential files or keys in the managed home; the live home was only read |
| Sessions in the Webapp | all 10 conversations with content listed with their titles, models and message counts, equal to the database. The 3 empty sessions (0 messages) are hidden by upstream |
| History | an old session opens with its history; a 462-message session renders in 3 s with "Show earlier messages" |
| Continuation (stub only) | works. The request the gateway sent carried the session's earlier turn, your memory (3/3 probed lines), the skills block and `SOUL.md` |
| Skills | 70 on disk, 63 listed. 6 of the 7 missing are macOS or Linux only and correctly filtered; `sdlc-review` declares Windows support and is not listed (reason not determined) |
| MCP | `node_repl` connected, 4 tools registered |
| Settings / model | all 23 imported config sections are equal to your live ones (model as migrated: `claude-sonnet-5-5[1m]` via the Claude plugin). Settings > Workspace writes `config.yaml`. The model picker switches a session to the stub |
| Project context | each session's working folder and repo root migrated (`E:\Project\Sahib-Android`, `E:\Project\Mihakk`, ...). Project tree, editor save to disk, Git stage and commit (`66b317d`) work. `E:` repos were deliberately not opened |
| Scheduled jobs / messaging | none exist / none enabled (the gateway logged "No messaging platforms enabled") |
| Navigation | Projects starts the gateway on demand (24 s cold, 2.4 s with it running); "← Personal" returns in 0.04 s with the Personal JS state and draft intact; the Webapp restored its own panels and composer draft |
| Logout | A: logout elsewhere while in Projects, the tab goes to the Odysseus login by itself. B: while signed in, Back restores the cached Personal page; after logout the first observation (0.48 s) was already the login form. The proxy answers a still-valid Hermes token with 401 `X-Odysseus-Grant: required`; the direct gateway port still accepts it (known limit). A stale flash shorter than 0.4 s cannot be excluded |

## What failed or needs attention
1. **Credentials outside the Hermes home were reachable.** The gateway runs as you, so it found your Claude Code and Codex logins in your user profile and queried Anthropic usage (the picker showed "12% left"). Nothing was copied, and `.credentials.json` was not modified, but this is the same-user limit made visible. For the rest of the rehearsal the gateway was started with an empty profile (`USERPROFILE` redirected); the Anthropic and Codex entries disappeared. **Production consequence:** your Claude and Codex subscriptions will work in the managed gateway without signing in again, because those logins live outside the Hermes home; decide whether that is what you want. A gateway restarted from the Odysseus Services menu uses Odysseus's environment, not an isolated profile.
2. **Resumed sessions use their own recorded model** (Claude, GPT-6.1-sol, longcat, deepseek). In the rehearsal the Claude plugin was neutralised (its command variable pointed at a missing file) and the default model was the stub. In production those sessions would call those providers.
3. `OPENCODE_ZEN_API_KEY` was not carried (by design): the longcat and deepseek sessions need it entered again.
4. The WebUI project groupings (Sahib Android, Masar, Sahib Desktop, Mihak) are not converted.
5. `sdlc-review` skill not listed (see above). The Webapp Plugins tab was only seen as a catalog; installed plugins were verified through config and the plugin folders and the gateway's own plugin-update message.
6. Tooling gaps found (fixed or noted): `ensure` does not recreate a deleted home and `repair` rebuilds the whole release (12 minutes), so a fresh home was created with the component's own `_prepare_home`; stopping the gateway is refused while a session was touched in the last 5 minutes (`--force`); a killed `repair` leaves a half-built release that has to be removed by hand.

## Not verified
Real providers (not done on purpose), the `E:` repos and Btrfs behaviour, other browsers, and the `sdlc-review` skill.

## Is it ready for your personal preview?
Yes, against the synthetic stub model: the interface, your sessions, memory, skills and settings are all there. Use a **private window or a separate browser profile**: cookies are host-wide, not port-wide, so logging in to the preview on `localhost:19800` in your everyday profile would replace your production `odysseus_session` cookie for `localhost:7000`.

## Remaining blockers to production
1. `websockets` in the prod venv plus `--ws websockets` on the launcher (your approval).
2. The 12-minute managed install on production (your approval).
3. Decide about point 1 above (outside logins) and enter the opencode key if you use those models.
4. `HERMES_WRITE_SAFE_ROOT` is still broad (your action).
5. Same-user code (terminal pane, approved tools, `npx`/`uvx` MCP servers, the direct gateway port) is not isolated.
6. A rehearsal with a real provider, once you choose.

## Exact rollback
- **Rehearsal:** `python F:\Odysseus-Backups\desktop-lab\tools\rehearsal_stop.py` stops all three processes and keeps the data; add `--delete-data` to also delete the migrated copy of your data, the scratch profile and the preview state.
- **Production:** nothing was changed, so nothing to roll back. For the real migration: the safety copy `<root>\migration\backups\pre-migration-*.zip` and `hermes-migrate.py rollback` as in `hermes-production-migration.md`.

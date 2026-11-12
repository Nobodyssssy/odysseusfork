# Production cutover: exact actions and the decisions still open

Status: **prepared, not executed.** Nothing was installed, restarted, pushed or changed in production, and no credential was touched. All checks below were read-only. The disposable preview on `localhost:19800` is still running for your inspection.

Code: `feature/webapp-proxy` (local only). Tools: `scripts/hermes-production.py` (plan, env-on, env-off), `hermes-release-check.py`, `hermes-webapp-component.py`, `hermes-migrate.py`, `odysseus-backup.py`. Decisions are recorded in `config/production-cutover.json`: only the approved roots and logins are ever applied.

## 1. Writable project folders

Verified read-only (file reads of `.git` and `HEAD` plus a volume query; no scan, no `git status`, no writes):

| Folder | Result | Detail |
|---|---|---|
| `E:\Project\Sahib-Android` | **valid, approved** | the exact root of a **linked Git worktree**, branch `codex/android-mobile`, on **Btrfs**. Its Git data lives in `C:\Users\Protagonist\source\repos\adkar\.git` (NTFS): a commit there also writes to that repository, which is not a write root and which the route guard does not check (it checks the working folder). Writes to the working files hit Btrfs, Git object writes hit NTFS |
| `E:\Project\Mihakk` | **valid, approved** | a normal repository, branch `main`, remote `origin`, entirely on **Btrfs** |
| `C:\Users\Protagonist\source\repos\adkar` | **candidate for "Sahib Desktop", waiting for you** | main checkout of the Sahib repository (origin `Nobodyssssy/sahib`), branch `codex/windows-tauri`, NTFS. Hints only: it is the Tauri desktop line, it is the parent of the Sahib-Android worktree, and it is in your current `HERMES_WRITE_SAFE_ROOT`. No recorded session names it, so I did not add it |
| Masar | **not found** | no repository by that name under `E:\Project` or `source\repos`, and no recorded session or WebUI folder points at one. I need the path |

Rules now enforced (and refused live): a root must be the **exact top folder of one Git repository**. Refused: `E:\`, `E:\Project`, `F:\Odysseus` (the live installation, including the main checkout when the code runs from a worktree), `%LOCALAPPDATA%\hermes`, `.claude`, `.codex`, `.ssh`, `.aws`, `.gnupg`, `AppData`, drive roots and your whole profile. `roots --check-root <folder>` reports on any candidate without changing anything.

Separate from the old WebUI: the managed gateway's list lives in its own component config and becomes that gateway's `HERMES_WRITE_SAFE_ROOT`. Your Windows user variable (used by the old WebUI and old gateway) is never read for this, written or changed. It currently lists `adkar`, `Sahib-Android`, `F:\Odysseus`, `F:\hermes-webui`, `F:\Odysseus-Backups` and two Hermes folders; the managed gateway gets none of the broad ones.

**Btrfs.** Nothing was written to `E:` during preflight. The managed gateway will write there only when you (or the agent) edit, commit or build in those two repositories. The WinBtrfs freeze risk is unchanged by this migration; the practical mitigation is to keep big builds, large checkouts and `git gc` out of `E:` and to keep the kernel-dump shortcut ready. The plan prints this note next to each Btrfs root.

## 2. Claude and Codex

Your decision: allow both, only at the authorized cutover (step 5), credentials stay where they are.

- Until step 5 both are **isolated** (Hermes's own code cannot find them). Step 5 runs `logins --allow claude --allow codex`; `logins --deny ...` reverses it.
- Credentials are never copied into component releases, migration bundles, project folders, or any Claude/Codex backup. They are read in place from `~/.claude` and `~/.codex`.
- **Corrected earlier statement:** with Claude allowed, the gateway can refresh the single-use OAuth token and **rewrite `~/.claude/.credentials.json`** (`_refresh_oauth_token` / `_write_claude_code_credentials`). Your existing Hermes does this today. The refresh is serialised across Hermes processes by a lock keyed to the credential file (`.credentials.lock` next to it), so the old gateway and the managed gateway will not collide; it is **not** coordinated with `claude.exe`, and upstream adopts an already-rotated token first. In the worst case a race leaves one side needing a fresh `claude login`.
- **Same-user limit, stated plainly:** isolation is not a security boundary. A shell command run by the agent, the terminal pane, an MCP server, or any other program running as you can read both login files whether or not the setting is on. See `hermes-windows-isolation.md`.

## 3. What is preserved

Conversations (13 sessions, 10,685 messages), memories, skills (63 of 70 shown; the other 7 are hidden by their own platform/Kanban settings), plugins, MCP server, model and provider selection, scheduled jobs (none exist), blocked-folder list and all settings, exactly as listed in `hermes-production-migration.md` and proven in the rehearsal. The live Hermes home is only read. Provider keys are not copied; enter `OPENCODE_ZEN_API_KEY` again. There is **no Hermes password** in this setup (correcting the earlier runbook): access is your Odysseus login plus the grant gate.

After the cutover the migrated copy and the old Hermes **diverge**: new Projects conversations live only in the managed home. Rolling back returns you to the old interface and the old data as of the bundle; the newer conversations are kept in the managed home but are not visible in the old WebUI and there is no tool to move them back.

## 4. Backup and rollback readiness (existing evidence, nothing re-run)

| Item | State |
|---|---|
| Full backup tool | `snapshot-20261111-130449-db3827` re-verified read-only today: 1,479 files, 907.7 MB, 9 databases OK. It predates this work, so step 1 makes a fresh one |
| Backup contents | Includes Hermes `.env` and `auth.json`, Odysseus `.env` and the WebUI signing key (stated in its manifest). **Open decision A below** |
| Migration safety copy | `apply` zips the managed home first; rollback drill proven in the rehearsal (home matched byte for byte) |
| One-line switch | `env-off --approved` plus restart; covered by unit tests (round trip restores the exact bytes) |
| Dependency | `pip install --dry-run` of the exact offline command resolves `websockets 17.4` from the hash-checked wheel; production packages unchanged; undo is `pip uninstall` |
| Code | pure fast-forward of `8b428243`; undo is `git reset --hard 8b428243` on a clean tree (the plan verifies it is clean) |
| Old WebUI | answers on 8787 and is never stopped or modified by any step |
| Restart | the launcher PID the restart will kill is shown by the plan; the task `Odysseus AutoStart` is the one used for the previous deployment |
| Release check | tool ready and validated against the earlier logs (reports CLEAN for the known 4 order-dependent tests, flags anything else); **not run**, by your instruction it runs once, at step 2 |

## 5. The exact production actions

Readiness as of this commit (`hermes-production.py plan`, read-only):

```
[OK   ] production checkout                              fix/windows-cookbook-delete @ 8b428243, clean
  [TODO ] code to deploy                                   feature/webapp-proxy @ 318bbb3f is 20 commit(s) ahead and a pure fast-forward
  [OK   ] websockets wheel staged                          websockets-17.4-cp312-cp312-win_amd64.whl sha256 matches the pin
  [TODO ] production venv                                  Python 3.12.10; websockets missing (pinned 17.4)
  [OK   ] offline install resolves (dry run, nothing installed) Would install websockets-17.4
  [OK   ] cutover tools import under the production Python ok
  [OK   ] .env switch                                      ODYSSEUS_HERMES_BACKEND is not set (old behaviour)
  [OK   ] Hermes interpreter for the managed gateway       C:\Users\Protagonist\AppData\Local\hermes\installs\562e58e2ea9aca80\environments\6eff8cc60cb848909fbabec0b09c88c2\venv\Scripts\python.exe
  [TODO ] managed Hermes component                         not installed under C:\Users\Protagonist\AppData\Local\Odysseus\hermes
  [OK   ] disk for the install                             63.2 GB free on C:\ (about 1.3 GB needed)
  [TODO ] write root (approved)                            E:\Project\Sahib-Android: valid, linked worktree, branch codex/android-mobile, Btrfs; added at step 5
  [NOTE ]                                                  Git data of this worktree lives OUTSIDE the folder, in C:\Users\Protagonist\source\repos\adkar\.git: a commit there also writes to that repository (the route guard checks the working folder only)
  [NOTE ]                                                  Btrfs (WinBtrfs): heavy writes (large checkouts, builds, git gc) can trigger the known driver freeze; keep such work off this drive
  [TODO ] write root (approved)                            E:\Project\Mihakk: valid, repository, branch main, Btrfs; added at step 5
  [NOTE ]                                                  Btrfs (WinBtrfs): heavy writes (large checkouts, builds, git gc) can trigger the known driver freeze; keep such work off this drive
  [WAIT ] write root: Sahib Desktop                        candidate C:\Users\Protagonist\source\repos\adkar (repository, branch codex/windows-tauri, NTFS); NOT added until you approve it
  [WAIT ] write root: Masar                                no repository found; tell me the path (it will be verified read-only first)
  [TODO ] Claude/Codex logins                              claude, codex allowed at step 5 (isolated until then); credentials stay where they are
  [OK   ] latest backup on disk                            snapshot-20261111-130449-db3827 (2026-11-11T13:05); a fresh one is made and verified at step 1
  [OK   ] old Hermes WebUI (the rollback)                  answers on 8787
  [OK   ] Odysseus                                         answers on 7000
  [OK   ] port 9130 for the managed gateway                free
  [OK   ] restart target (Odysseus launcher)               PID 12340: "C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe" -NoProfile -NonInteractive -ExecutionPolicy Bypass
  [NOTE ] old WebUI write roots (unchanged, separate)      C:\Users\Protagonist\source\repos\adkar;E:\Project\Sahib-Android;F:\Odysseus;F:\hermes-webui;F:\Odysseus-Backups;C:\Users\Protagonist\AppData\Local\Odysseus\hermes;C:\Users\Protagonist\AppData\Local\hermes\webui

0 blocker(s); 6 step(s) to do at cutover; 2 waiting for your decision.
```

```
PROCEDURE after you say GO (re-run this plan first: every row OK; then one step at a time, each can be undone, stop after any of them)
  1 BACKUP         F:\Odysseus\venv\Scripts\python.exe F:\Odysseus\integration-backups\webapp-proxy\scripts\odysseus-backup.py --backup-dir F:\Odysseus-Backups create
                   F:\Odysseus\venv\Scripts\python.exe F:\Odysseus\integration-backups\webapp-proxy\scripts\odysseus-backup.py --backup-dir F:\Odysseus-Backups verify <snapshot name printed by create>
  2 RELEASE CHECK  F:\Odysseus-Backups\desktop-lab\ody-full-venv\Scripts\python.exe F:\Odysseus\integration-backups\webapp-proxy\scripts\hermes-release-check.py run --python F:\Odysseus-Backups\desktop-lab\ody-full-venv\Scripts\python.exe --repo F:\Odysseus\integration-backups\webapp-proxy --log F:\Odysseus-Backups\desktop-lab\logs\release-check.log      (about 16 minutes, once)
                   must print "RELEASE CHECK: CLEAN"; after it, no commit may be added to feature/webapp-proxy
  3 DEPENDENCY     F:\Odysseus\venv\Scripts\python.exe -m pip install --no-index --no-cache-dir --find-links F:\Odysseus-Backups\wheels\websockets-17.4 websockets==17.4
                   undo: F:\Odysseus\venv\Scripts\python.exe -m pip uninstall -y websockets
  4 GATEWAY        F:\Odysseus\venv\Scripts\python.exe F:\Odysseus\integration-backups\webapp-proxy\scripts\hermes-webapp-component.py ensure --origin managed --python C:\Users\Protagonist\AppData\Local\hermes\installs\562e58e2ea9aca80\environments\6eff8cc60cb848909fbabec0b09c88c2\venv\Scripts\python.exe --home C:\Users\Protagonist\AppData\Local\Odysseus\hermes\state\hermes-gateway --port 9130      (about 12 minutes; does not start it)
                   undo: delete C:\Users\Protagonist\AppData\Local\Odysseus\hermes\managed\webapp and C:\Users\Protagonist\AppData\Local\Odysseus\hermes\state\hermes-gateway
  5 AUTHORIZE      F:\Odysseus\venv\Scripts\python.exe F:\Odysseus\integration-backups\webapp-proxy\scripts\hermes-webapp-component.py roots --add-root E:\Project\Sahib-Android --add-root E:\Project\Mihakk
                   F:\Odysseus\venv\Scripts\python.exe F:\Odysseus\integration-backups\webapp-proxy\scripts\hermes-webapp-component.py logins --allow claude --allow codex
                   undo: roots --clear-roots ; logins --deny claude --deny codex
  6 DATA           F:\Odysseus\venv\Scripts\python.exe F:\Odysseus\integration-backups\webapp-proxy\scripts\hermes-migrate.py bundle --source C:\Users\Protagonist\AppData\Local\hermes --out F:\Odysseus-Backups\hermes-bundle-<yyyymmdd-hhmm> --python C:\Users\Protagonist\AppData\Local\hermes\installs\562e58e2ea9aca80\environments\6eff8cc60cb848909fbabec0b09c88c2\venv\Scripts\python.exe
                   F:\Odysseus\venv\Scripts\python.exe F:\Odysseus\integration-backups\webapp-proxy\scripts\hermes-migrate.py plan --bundle <that folder>        must say "problems": []
                   F:\Odysseus\venv\Scripts\python.exe F:\Odysseus\integration-backups\webapp-proxy\scripts\hermes-migrate.py apply --bundle <that folder> --confirm-target C:\Users\Protagonist\AppData\Local\Odysseus\hermes\state\hermes-gateway
                   F:\Odysseus\venv\Scripts\python.exe F:\Odysseus\integration-backups\webapp-proxy\scripts\hermes-migrate.py verify --bundle <that folder>
                   undo: hermes-migrate.py rollback --safety-copy <pre-migration zip printed by apply> --confirm-target C:\Users\Protagonist\AppData\Local\Odysseus\hermes\state\hermes-gateway
                   (do not start new chats in the old WebUI between the bundle and step 8: they would not be in the bundle)
  7 SWITCH         git -C F:\Odysseus merge --ff-only feature/webapp-proxy                                undo: git -C F:\Odysseus reset --hard 8b428243
                   F:\Odysseus\venv\Scripts\python.exe F:\Odysseus\integration-backups\webapp-proxy\scripts\hermes-production.py env-on --approved
                   taskkill /PID 12340 /T /F        then        Start-ScheduledTask -TaskName 'Odysseus AutoStart'
                   (needs: no active Odysseus chat or agent run; the old WebUI and the old gateway keep running)
  8 VERIFY         read-only on your real projects: Personal works; Projects starts Hermes; a migrated conversation opens with its history; the file tree and diff of E:\Project\Mihakk load;
                   logout ends Projects access. Editor save and commit were proven in the lab with this same code; try them only on a repository you choose.
ROLLBACK, immediately: hermes-production.py env-off --approved, then the same restart. The old WebUI on 8787 was never stopped or changed.
```

Order notes: the code fast-forward is deliberately step 7, right before the restart, because the static files are served live and would otherwise run ahead of the old backend. Steps 1 to 6 do not change anything the running Odysseus or the old Hermes uses (step 3 only adds a library nothing imports yet).

## 6. Decisions still open

1. **Sahib Desktop**: approve `C:\Users\Protagonist\source\repos\adkar` as a write root, or give another path. (Not added.)
2. **Masar**: give the repository path. (Not found; not added.)
3. **Backups and credentials (conflict with your rule).** The existing full backup includes Hermes `.env`/`auth.json`, Odysseus `.env` and the WebUI signing key; Claude/Codex logins are not included. Either **(A)** keep it (complete restore, but credentials sit in plain files in `F:\Odysseus-Backups`), or **(B)** I add an `--exclude-secrets` mode and use it for step 1 (the cutover never modifies the live Hermes home, so rollback does not depend on those copies; a restore after a disk loss would need keys re-entered). My recommendation is (B) for the cutover snapshot while keeping your existing snapshots as they are. Choosing (B) needs one more code change and test before step 1.
4. **Git data of the Sahib-Android worktree** lives outside its folder. Accept that commits there also write to `adkar`'s `.git` (the route guard still allows them), or authorize `adkar` as well (decision 1).
5. **Time of the cutover**: it needs about 45 minutes in total (release check 16, gateway install 12, the rest short) and no active Odysseus chat or agent run at the restart.

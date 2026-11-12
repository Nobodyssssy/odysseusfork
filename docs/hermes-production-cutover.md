# Production cutover: exact actions

Status: **prepared and waiting for your final GO.** Nothing was installed, restarted, pushed or changed in production. Scope: Odysseus plus the official Hermes Desktop Webapp only. No project repository is read, listed or touched by this migration, and the managed gateway's writable project roots are **empty**; you add a folder later with `hermes-webapp-component.py roots --add-root <folder>` (it accepts only the exact top folder of one Git repository, and never a credential folder, `F:\Odysseus`, a drive root or `E:\Project`).

Decisions are in `config/production-cutover.json`. Backups: `docs/hermes-backups-and-secrets.md`.

## Claude and Codex

Both are allowed at step 5 and isolated until then. Credentials stay in `~/.claude` and `~/.codex` and are never copied into releases, bundles or backups. With Claude allowed the gateway can refresh the single-use OAuth token and rewrite `~/.claude/.credentials.json` (your current Hermes already does this). The two Hermes processes use a lock keyed to that file, but `claude.exe` does not. To avoid a refresh race, **do not use Claude in the old WebUI/old gateway and the managed Webapp at the same time**, and do not run Claude Code and Hermes concurrently while a token is about to expire. Worst case is one fresh `claude login`. Same-user limit: any program running as you, including a shell command run by an agent, can read both login folders (see `hermes-windows-isolation.md`).

## What is preserved and what is not

Preserved: conversations (13 sessions), memories, skills (63 shown, 7 hidden by their own platform settings), plugins, the MCP server, model/provider selection, protected-folder list, settings. Not transferred: provider API keys and logins (re-enter `OPENCODE_ZEN_API_KEY`), old session tokens and grants, WebUI project groupings, process state. There is no Hermes password in the managed setup. After the cutover, new Projects conversations exist only in the managed home.

## Readiness (read-only `hermes-production.py plan`, 2026-11-12)

```
  [OK   ] production checkout                              fix/windows-cookbook-delete @ 8b428243, clean
  [TODO ] code to deploy                                   feature/webapp-proxy @ a173babc is 21 commit(s) ahead and a pure fast-forward
  [OK   ] websockets wheel staged                          websockets-17.4-cp312-cp312-win_amd64.whl sha256 matches the pin
  [TODO ] production venv                                  Python 3.12.10; websockets missing (pinned 17.4)
  [OK   ] offline install resolves (dry run, nothing installed) Would install websockets-17.4
  [OK   ] cutover tools import under the production Python ok
  [OK   ] .env switch                                      ODYSSEUS_HERMES_BACKEND is not set (old behaviour)
  [OK   ] Hermes interpreter for the managed gateway       C:\Users\Protagonist\AppData\Local\hermes\installs\562e58e2ea9aca80\environments\6eff8cc60cb848909fbabec0b09c88c2\venv\Scripts\python.exe
  [TODO ] managed Hermes component                         not installed under C:\Users\Protagonist\AppData\Local\Odysseus\hermes
  [OK   ] disk for the install                             62.4 GB free on C:\ (about 1.3 GB needed)
  [OK   ] managed gateway write roots                      none: you authorize project folders later; nothing is read or touched on any project
  [TODO ] Claude/Codex logins                              claude, codex allowed at step 5 (isolated until then); credentials stay where they are
  [OK   ] latest backup on disk                            snapshot-20261111-130449-db3827 (2026-11-11T13:05); a fresh one is made and verified at step 1
  [OK   ] old Hermes WebUI (the rollback)                  answers on 8787
  [OK   ] Odysseus                                         answers on 7000
  [OK   ] port 9130 for the managed gateway                free
  [OK   ] restart target (Odysseus launcher)               PID 12340: "C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe" -NoProfile -NonInteractive -ExecutionPolicy Bypass
  [NOTE ] old WebUI write roots (unchanged, separate)      C:\Users\Protagonist\source\repos\adkar;E:\Project\Sahib-Android;F:\Odysseus;F:\hermes-webui;F:\Odysseus-Backups;C:\Users\Protagonist\AppData\Local\Odysseus\hermes;C:\Users\Protagonist\AppData\Local\hermes\webui

0 blocker(s); 4 step(s) to do at cutover; 0 waiting for your decision.
```

## Procedure after your GO (each step has its own undo; stop after any of them)

```
PROCEDUREafter you say GO (re-run this plan first: every row OK; then one step at a time, each can be undone, stop after any of them)
  1 BACKUP         F:\Odysseus\venv\Scripts\python.exe F:\Odysseus\integration-backups\webapp-proxy\scripts\odysseus-backup.py --backup-dir F:\Odysseus-Backups --odysseus-root F:\Odysseus --data-dir F:\Odysseus\data create --exclude-secrets
                   (no credential file is copied and secret values are removed from the copies; your existing full backups stay as they are. Rollback does not need the secrets: nothing in the live Hermes home or Odysseus is modified)
                   F:\Odysseus\venv\Scripts\python.exe F:\Odysseus\integration-backups\webapp-proxy\scripts\odysseus-backup.py --backup-dir F:\Odysseus-Backups --odysseus-root F:\Odysseus --data-dir F:\Odysseus\data verify <snapshot name printed by create>
  2 RELEASE CHECK  F:\Odysseus-Backups\desktop-lab\ody-full-venv\Scripts\python.exe F:\Odysseus\integration-backups\webapp-proxy\scripts\hermes-release-check.py run --python F:\Odysseus-Backups\desktop-lab\ody-full-venv\Scripts\python.exe --repo F:\Odysseus\integration-backups\webapp-proxy --log F:\Odysseus-Backups\desktop-lab\logs\release-check.log      (about 16 minutes, once)
                   must print "RELEASE CHECK: CLEAN"; after it, no commit may be added to feature/webapp-proxy
  3 DEPENDENCY     F:\Odysseus\venv\Scripts\python.exe -m pip install --no-index --no-cache-dir --find-links F:\Odysseus-Backups\wheels\websockets-17.4 websockets==17.4
                   undo: F:\Odysseus\venv\Scripts\python.exe -m pip uninstall -y websockets
  4 GATEWAY        F:\Odysseus\venv\Scripts\python.exe F:\Odysseus\integration-backups\webapp-proxy\scripts\hermes-webapp-component.py ensure --origin managed --python C:\Users\Protagonist\AppData\Local\hermes\installs\562e58e2ea9aca80\environments\6eff8cc60cb848909fbabec0b09c88c2\venv\Scripts\python.exe --home C:\Users\Protagonist\AppData\Local\Odysseus\hermes\state\hermes-gateway --port 9130      (about 12 minutes; does not start it)
                   undo: delete C:\Users\Protagonist\AppData\Local\Odysseus\hermes\managed\webapp and C:\Users\Protagonist\AppData\Local\Odysseus\hermes\state\hermes-gateway
  5 AUTHORIZE      F:\Odysseus\venv\Scripts\python.exe F:\Odysseus\integration-backups\webapp-proxy\scripts\hermes-webapp-component.py logins --allow claude --allow codex      (write roots stay empty: Projects can read nothing it was not given and write nothing until you add a folder)
                   undo: logins --deny claude --deny codex
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
  8 VERIFY         no project is touched: Personal works; Projects starts Hermes; a migrated conversation opens with its history; the model picker, memory and skills show;
                   logout ends Projects access. Editing a repository is tested when you add a folder.
ROLLBACK, immediately: hermes-production.py env-off --approved, then the same restart. The old WebUI on 8787 was never stopped or changed.
```

Step 7 is last on purpose: static files are served live and would run ahead of the old backend. Steps 1 to 6 change nothing the running Odysseus or old Hermes uses.

## Time and conditions

About 45 minutes (release check 16, gateway install 12, the rest short). No active Odysseus chat or agent run at the restart. The release check runs once at step 2 and no commit is made after it.

## Known gap, for when you add a project folder

A linked Git worktree keeps its Git data in another repository. The route guard checks the working folder you authorize, not that other location, so commits in a worktree also write there. Handling this (verify the real Git directory and common directory against the authorized roots) is not built because no project is authorized now; it must be done before a worktree is added.

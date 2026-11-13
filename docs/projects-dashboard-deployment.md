# Projects dashboard: first production deployment (prepared, NOT executed)

Code: branch `feature/projects-dashboard` (check in step 0 that production is an ancestor, so the merge is a fast-forward). The head to expect is `git rev-parse --short feature/projects-dashboard`.
Activation state after deploy: dashboard and its read-only API ON; grant/revoke OFF (`ODYSSEUS_PROJECTS_GRANTS` unset). No gateway restart, no change to grants, WebUI patch, Wiki Steward or `odysseus-return`.

Run from PowerShell. `$G` = `C:\Program Files\Git\cmd\git.exe`. Stop and report on any unexpected output.

## 0. Preflight (read-only)
```
cd F:\Odysseus
& $G status --short                       # only the known untracked .env.pre-projects-* file
& $G rev-parse --short HEAD               # the production head to record (it is the rollback target)
& $G merge-base --is-ancestor HEAD feature/projects-dashboard; $LASTEXITCODE   # expect 0
curl.exe -s http://127.0.0.1:9130/api/status   # no active runs; do not restart the app while one is running
```

## 1. Backup (outside every write root; all small)
```
$B = "F:\Odysseus-Backups\projects-dashboard-deploy-$(Get-Date -Format yyyyMMdd-HHmm)"; New-Item -ItemType Directory $B | Out-Null
& $G -C F:\Odysseus rev-parse HEAD | Set-Content $B\prod-head.txt
Copy-Item "$env:LOCALAPPDATA\Odysseus\hermes\managed\webapp\component.json" $B\component.json
Copy-Item "$env:LOCALAPPDATA\Odysseus\hermes\state\hermes-gateway\desktop-plugins" $B\desktop-plugins -Recurse
Copy-Item F:\Odysseus\.env $B\env.bak
(Get-FileHash $B\component.json).Hash | Set-Content $B\component.json.sha256
```

## 2. Merge (fast-forward only, no push)
```
cd F:\Odysseus
& $G merge --ff-only feature/projects-dashboard
& $G rev-parse --short HEAD               # equals the branch head
```

## 3. Resume plugin: automatic install
The component now installs `odysseus-resume` (and keeps `odysseus-return` as is) every time it starts the gateway, and `ensure` also does it. To install now without touching the running gateway, run `start`: it installs the plugins first, then reports the gateway is already ready and returns.
```
cd F:\Odysseus
venv\Scripts\python.exe -W ignore scripts\hermes-webapp-component.py --root "$env:LOCALAPPDATA\Odysseus\hermes" start
Test-Path "$env:LOCALAPPDATA\Odysseus\hermes\state\hermes-gateway\desktop-plugins\odysseus-resume\plugin.js"   # True
(Get-FileHash "$env:LOCALAPPDATA\Odysseus\hermes\managed\webapp\component.json").Hash   # equals $B\component.json.sha256 (start does not rewrite it)
```
Pre-verified read-only against the real home: no conflict; return plugin is installed (LF) and counts as current although the checkout is CRLF.

## 4. Controlled Odysseus restart (app only; the gateway is protected)
Process hierarchy, as observed on this machine: `Odysseus AutoStart` task -> launcher `powershell` -> supervisor `python -m uvicorn app:app` (stub) -> app (:7000) -> its MCP servers
(email, rag, memory, image, playwright, ...). The managed Hermes gateway (:9130) is NOT guaranteed to be outside that tree: when the app itself started it, the gateway is a descendant
of the app, so `taskkill /F /T` on the stub (the earlier version of this step) kills the gateway and any run in progress. Use the script, which finds the gateway's branch and keeps it.
```
cd F:\Odysseus
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\restart-odysseus-app.ps1          # dry run: lists what is stopped and what is kept, and the number of running turns
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\restart-odysseus-app.ps1 -Apply   # stops the app + its non-gateway children + its supervisor, starts the task, waits for :7000
```
- It refuses to continue when it cannot account for the gateway, when the plan would stop the gateway, or (without `-Force`) when a turn is running (unexpired row in `session_turn_leases`
  of the gateway's `state.db`, read-only). Use `-Force` only after looking at which conversation is running.
- It prints the gateway PID before and after and exits non-zero if it changed. If the gateway was started by the app and is inside its tree, it stays running as an orphan of the old app;
  nothing starts a second one because the new app finds :9130 healthy.
- It never touches `chroma`, the old WebUI (:8787) or any LongCat process; those are not in the app's tree.
- If the gateway is stopped for any reason (check `netstat -ano | findstr :9130`), start it with `venv\Scripts\python.exe -W ignore scripts\hermes-webapp-component.py --root "%LOCALAPPDATA%\Odysseus\hermes" start`
  and tell the user first: it interrupts anything running in it.

## 5. Verify
```
curl.exe -s -o NUL -w "%{http_code}`n" http://localhost:7000/                                  # 302
curl.exe -s -o NUL -w "%{http_code}`n" http://localhost:7000/api/projects-dashboard           # 401 (not signed in)
Get-NetTCPConnection -State Listen -LocalPort 7000 | % { (Get-Process -Id $_.OwningProcess).StartTime }   # now
Get-NetTCPConnection -State Listen -LocalPort 9130 | % { $_.OwningProcess }   # same PID as printed by the restart script
Get-Content "$env:LOCALAPPDATA\Odysseus\hermes\state\hermes-gateway\odysseus-workspace-grants.status.json"  # installed, grants 1
Select-String -Path "$env:LOCALAPPDATA\Odysseus\hermes\state\hermes-gateway\logs\agent.log" -Pattern "reconciled" | Select -Last 1   # wiki present
```

## 6. One authenticated Firefox check (you, signed in; I cannot sign in for you)
1. Open `http://localhost:7000`, sign in, click **Dashboard** in the top bar.
2. Select **Sahib Android**, **Sessions** tab, click **Resume** on "Continue Sahib Android development from existing state" (a Hermes conversation).
3. Expected: a new tab signs in to the Webapp and opens that conversation (not the start page). If it opens the start page, the plugin was not loaded: reload that tab once.
4. Also check the **Access** tab shows "Granting and revoking from this page is switched off".

## 7. Rollback (code and plugin only; nothing else was changed)
```
cd F:\Odysseus
& $G reset --hard (Get-Content $B\prod-head.txt)          # the head recorded in step 1; the working tree was clean in step 0
$P = "$env:LOCALAPPDATA\Odysseus\hermes\state\hermes-gateway\desktop-plugins\odysseus-resume"
if (Test-Path $B\desktop-plugins\odysseus-resume) { Copy-Item $B\desktop-plugins\odysseus-resume\plugin.js $P\plugin.js -Force } else { Remove-Item $P -Recurse }   # restore the previous plugin copy, or remove it if there was none
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\restart-odysseus-app.ps1 -Apply     # the same app-only restart
```
If step 3 somehow changed `component.json`: `Copy-Item $B\component.json "$env:LOCALAPPDATA\Odysseus\hermes\managed\webapp\component.json"` (the grant list is inside; the hash in `$B` proves the original).

## Later activation of grant/revoke (separate approval)
Add `ODYSSEUS_PROJECTS_GRANTS=1` to `F:\Odysseus\.env`, then run the step 4 script. Disable by removing the line and restarting. With authentication disabled the server refuses grant calls regardless of the switch.

# Projects dashboard: first production deployment (prepared, NOT executed)

Code: branch `feature/projects-dashboard` at `d2e76e42` (fast-forward of production `F:\Odysseus` at `aab07e53`; verified with `merge-base --is-ancestor`).
Activation state after deploy: dashboard and its read-only API ON; grant/revoke OFF (`ODYSSEUS_PROJECTS_GRANTS` unset). No gateway restart, no change to grants, WebUI patch, Wiki Steward or `odysseus-return`.

Run from PowerShell. `$G` = `C:\Program Files\Git\cmd\git.exe`. Stop and report on any unexpected output.

## 0. Preflight (read-only)
```
cd F:\Odysseus
& $G status --short                       # only the known untracked .env.pre-projects-* file
& $G rev-parse --short HEAD               # expect aab07e53
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
& $G rev-parse --short HEAD               # expect d2e76e42
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
```
$app = (Get-NetTCPConnection -State Listen -LocalPort 7000).OwningProcess
$stub = (Get-CimInstance Win32_Process -Filter "ProcessId=$app").ParentProcessId
$launcher = (Get-CimInstance Win32_Process -Filter "ProcessId=$stub").ParentProcessId
$gw = (Get-NetTCPConnection -State Listen -LocalPort 9130).OwningProcess
"app=$app stub=$stub launcher=$launcher gateway=$gw"
# the gateway must NOT be a descendant of $stub; if it is, stop here and report
Get-CimInstance Win32_Process | Where-Object { $_.ParentProcessId -eq $stub -or $_.ProcessId -eq $stub } | Select ProcessId,Name
taskkill /F /T /PID $stub                                  # app + its own MCP children only
while (Get-Process -Id $launcher -ErrorAction SilentlyContinue) { Start-Sleep 2 }   # the launcher exits by itself
Start-ScheduledTask -TaskName 'Odysseus AutoStart'
```

## 5. Verify
```
curl.exe -s -o NUL -w "%{http_code}`n" http://localhost:7000/                                  # 302
curl.exe -s -o NUL -w "%{http_code}`n" http://localhost:7000/api/projects-dashboard           # 401 (not signed in)
Get-NetTCPConnection -State Listen -LocalPort 7000 | % { (Get-Process -Id $_.OwningProcess).StartTime }   # now
Get-NetTCPConnection -State Listen -LocalPort 9130 | % { (Get-Process -Id $_.OwningProcess).StartTime }   # unchanged (gateway untouched)
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
& $G reset --hard (Get-Content $B\prod-head.txt)          # aab07e53; the working tree was clean in step 0
Remove-Item "$env:LOCALAPPDATA\Odysseus\hermes\state\hermes-gateway\desktop-plugins\odysseus-resume" -Recurse
# repeat the step 4 restart block
```
If step 3 somehow changed `component.json`: `Copy-Item $B\component.json "$env:LOCALAPPDATA\Odysseus\hermes\managed\webapp\component.json"` (the grant list is inside; the hash in `$B` proves the original).

## Later activation of grant/revoke (separate approval)
Add `ODYSSEUS_PROJECTS_GRANTS=1` to `F:\Odysseus\.env`, repeat the step 4 restart. Disable by removing the line and restarting. With authentication disabled the server refuses grant calls regardless of the switch.

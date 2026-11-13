<#
Restart only the Odysseus web app (port 7000) and leave the Hermes gateway (port 9130) and everything under it running.

The managed gateway can sit INSIDE the app's process tree (the app starts it), so `taskkill /T` on the app's stub kills it and any run in progress. This
script works out which part of the tree holds the gateway and protects that whole branch. Nothing is stopped unless -Apply is given; without it the script
prints what it would stop and what it would keep, and refuses to continue when it cannot account for the gateway.

  .\scripts\restart-odysseus-app.ps1            # dry run: prints the plan
  .\scripts\restart-odysseus-app.ps1 -Apply     # stops the app, starts the 'Odysseus AutoStart' task, waits for :7000 and checks :9130 kept its PID
#>
[CmdletBinding()]
param([switch]$Apply, [switch]$Force, [int]$AppPort = 7000, [int]$GatewayPort = 9130, [string]$Task = 'Odysseus AutoStart')

$ErrorActionPreference = 'Stop'
$procs = @{}
Get-CimInstance Win32_Process | ForEach-Object { $procs[[int]$_.ProcessId] = $_ }

function Listener([int]$port) {
    $c = Get-NetTCPConnection -State Listen -LocalPort $port -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($c) { [int]$c.OwningProcess } else { $null }
}
function Children([int]$parent) { $procs.Values | Where-Object { [int]$_.ParentProcessId -eq $parent -and [int]$_.ProcessId -ne $parent } | ForEach-Object { [int]$_.ProcessId } }
function Subtree([int]$root) {
    $out = New-Object System.Collections.Generic.List[int]; $out.Add($root)
    for ($i = 0; $i -lt $out.Count; $i++) { foreach ($c in (Children $out[$i])) { if (-not $out.Contains($c)) { $out.Add($c) } } }
    $out
}
function Describe([int]$id) { $p = $procs[$id]; if ($p) { '{0,6} {1,-14} {2}' -f $id, $p.Name, ([string]$p.CommandLine).Substring(0, [Math]::Min(90, ([string]$p.CommandLine).Length)) } else { "$id (gone)" } }

$app = Listener $AppPort
$gateway = Listener $GatewayPort
if (-not $app) { throw "Nothing is listening on :$AppPort; start the '$Task' task instead." }
Write-Host "app pid $app, gateway pid $gateway"

# Everything above the app that exists only to run it (the stub that supervises it). The launcher above that exits by itself.
$stub = [int]$procs[$app].ParentProcessId
if (-not $procs.ContainsKey($stub) -or $procs[$stub].Name -notmatch 'python') { throw "Unexpected parent of the app ($stub); refusing to guess." }

$appTree = Subtree $app
$protectedRoot = $null
if ($gateway) {
    $cursor = $gateway
    while ($cursor -and $cursor -ne $app) {
        $parent = [int]$procs[$cursor].ParentProcessId
        if ($parent -eq $app) { $protectedRoot = $cursor; break }
        $cursor = $parent
    }
    if (-not $protectedRoot -and ($appTree -contains $gateway)) { $protectedRoot = $gateway }
}
$protected = if ($protectedRoot) { Subtree $protectedRoot } else { @() }
if ($gateway -and -not $protectedRoot) { Write-Host "The gateway is not inside the app's tree; nothing of it is at risk." }
if ($protectedRoot) { Write-Host "Protected branch (gateway inside the app tree), kept running:"; $protected | ForEach-Object { '  keep ' + (Describe $_) } }

$stop = @($appTree | Where-Object { $protected -notcontains $_ })
Write-Host "Will stop:"; $stop | ForEach-Object { '  stop ' + (Describe $_) }
Write-Host ('  stop ' + (Describe $stub) + '   (supervisor of the app only)')
if ($gateway -and ($stop -contains $gateway -or $stub -eq $gateway)) { throw 'The plan would stop the gateway; refusing.' }

# Turns that are running right now (an unexpired lease in the gateway's own database). Read-only. The restart itself does not touch the gateway, but the app is
# the page the user watches those runs through, so it is only restarted when nothing is running unless -Force is given.
$python = 'F:\Odysseus\venv\Scripts\python.exe'
$db = Join-Path $env:LOCALAPPDATA 'Odysseus\hermes\state\hermes-gateway\state.db'
$running = '?'
if ((Test-Path $db) -and (Test-Path $python)) {
    $code = "import sqlite3,sys,time;c=sqlite3.connect('file:'+sys.argv[1].replace(chr(92),'/')+'?mode=ro',uri=True);print(c.execute('select count(*) from session_turn_leases where expires_at>?',(time.time(),)).fetchone()[0])"
    $running = (& $python -c $code $db).Trim()
}
Write-Host "Turns running right now: $running"
if ($Apply -and $running -ne '0' -and -not $Force) { throw "Turns are running ($running, '?' means unknown). Wait for them, or re-run with -Force after checking they are not LongCat work you care about." }

if (-not $Apply) { Write-Host 'Dry run only. Re-run with -Apply to restart.'; return }

# Children first, branch by branch (never /T across the protected branch), then the app, then its supervisor.
foreach ($id in ($stop | Where-Object { $_ -ne $app })) { if (Get-Process -Id $id -ErrorAction SilentlyContinue) { & taskkill.exe /F /PID $id | Out-Null } }
& taskkill.exe /F /PID $app | Out-Null
if (Get-Process -Id $stub -ErrorAction SilentlyContinue) { & taskkill.exe /F /PID $stub | Out-Null }
for ($i = 0; $i -lt 20 -and (Listener $AppPort); $i++) { Start-Sleep -Milliseconds 500 }
Start-Sleep -Seconds 2
Start-ScheduledTask -TaskName $Task

$up = $false
for ($i = 0; $i -lt 60; $i++) {
    Start-Sleep -Seconds 2
    try { $r = Invoke-WebRequest -UseBasicParsing -MaximumRedirection 0 -Uri "http://localhost:$AppPort/" -ErrorAction Stop; $up = $true; break }
    catch { if ($_.Exception.Response -and [int]$_.Exception.Response.StatusCode -in 301, 302, 303, 307, 308) { $up = $true; break } }
}
$after = Listener $GatewayPort
Write-Host ("app up: {0}; new app pid {1}; gateway pid before {2}, after {3} ({4})" -f $up, (Listener $AppPort), $gateway, $after, $(if ($gateway -eq $after) { 'unchanged' } else { 'CHANGED' }))
if (-not $up -or $gateway -ne $after) { exit 1 }

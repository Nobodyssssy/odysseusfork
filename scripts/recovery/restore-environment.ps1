<#
.SYNOPSIS  Guided restore on a fresh Windows PC. Dry run unless -Apply. Never deletes, never starts services.
.DESCRIPTION
 Run from a fresh clone of your Odysseus fork, after Python 3.12 and Git are installed.
 Steps: (1) preflight, (2) restore Odysseus + managed Hermes from a snapshot, (3) restore git bundles,
 (4) install personal skills, (5) print the manual checklist.
.EXAMPLE
 .\restore-environment.ps1 -RecoveryFolder E:\Recovery\recovery-YYYYMMDD-HHMMSS -Snapshot E:\Recovery\snapshot-YYYYMMDD-HHMMSS-xxxxxx            # dry run
 .\restore-environment.ps1 -RecoveryFolder ... -Snapshot ... -SkillsRepo F:\hermes-personal-skills -Apply
#>
param(
  [Parameter(Mandatory)][string]$Snapshot,                  # folder made by odysseus-backup.py create
  [string]$RecoveryFolder,                                  # folder made by backup-extras.ps1
  [string]$OdysseusRoot,
  [string]$RepoRestoreRoot = 'F:\Restored',                 # where git bundles are cloned
  [string]$SkillsRepo,                                      # your private hermes-personal-skills clone
  [int[]]$Ports = @(7000, 8100, 8787, 9130),                # must be free during a restore
  [switch]$RestoreTasks,                                    # re-register Windows scheduled tasks (asks first)
  [switch]$Apply
)
$ErrorActionPreference = 'Stop'
function Step($n, $t) { Write-Host "`n== Step ${n}: $t" -ForegroundColor Cyan }
if (-not $OdysseusRoot) { $OdysseusRoot = Split-Path -Parent (Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)) }
$OdysseusRoot = (Resolve-Path $OdysseusRoot).Path
$cli = Join-Path $OdysseusRoot 'scripts\odysseus-backup.py'
Step 1 'Preflight'
foreach ($tool in 'python', 'git') { if (-not (Get-Command $tool -ErrorAction SilentlyContinue)) { throw "$tool is not installed. Install it first (see RESTORE_GUIDE.md section 2)." } }
if (-not (Test-Path (Join-Path $Snapshot 'manifest.json'))) { throw "No manifest.json in $Snapshot. Is this a snapshot folder (and is it decrypted)?" }
python $cli verify $Snapshot
if ($LASTEXITCODE -ne 0) { throw 'The snapshot failed verification. Do not restore from it.' }
$busy = foreach ($port in $Ports) { if (Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue) { "port $port" } }
if ($busy) { Write-Warning "Still running: $($busy -join ', '). Stop Odysseus, Hermes and Chroma first (a restore must be offline)."; if ($Apply) { throw 'Refusing to restore while services run.' } }

Step 2 'Odysseus data, settings, memory, databases and managed Hermes home'
$flags = @('--hermes-home', (Join-Path $env:LOCALAPPDATA 'hermes'), 'restore', $Snapshot); if ($Apply) { $flags += '--apply' }; if ($RestoreTasks -and $Apply) { $flags += '--restore-tasks' }
python $cli @flags

Step 3 'Unpublished git work (bundles)'
if ($RecoveryFolder -and (Test-Path (Join-Path $RecoveryFolder 'git'))) {
    foreach ($b in Get-ChildItem (Join-Path $RecoveryFolder 'git') -Filter *.bundle) {
        $target = Join-Path $RepoRestoreRoot $b.BaseName
        if ((Test-Path $target)) { Write-Host "  exists, left alone: $target"; continue }
        Write-Host "  $($b.Name) -> $target"
        if ($Apply) { New-Item -ItemType Directory $RepoRestoreRoot -Force | Out-Null; git clone --quiet $b.FullName $target; git -C $target config advice.detachedHead false }
    }
    Get-ChildItem (Join-Path $RecoveryFolder 'git') -Filter *-uncommitted.patch | ForEach-Object { Write-Host "  uncommitted edits saved in $($_.Name): apply with 'git apply' in the matching repo if you want them" }
} else { Write-Host '  (no -RecoveryFolder given: skipped)' }

Step 4 'Personal skills and Alyn MCP'
if ($SkillsRepo -and (Test-Path $SkillsRepo)) {
    $skillsHome = Join-Path $env:LOCALAPPDATA 'Odysseus\hermes\state\hermes-gateway'
    $a = @{ Target = $skillsHome; Groups = 'all'; External = $true; AlynMcp = $true; Apply = [bool]$Apply }
    & (Join-Path $SkillsRepo 'scripts\install.ps1') @a
} else { Write-Host '  (no -SkillsRepo given: skipped; clone your private hermes-personal-skills first)' }

Step 5 'Still manual (nothing here can be restored from a backup)'
@('Sign in to Odysseus again (admin account) unless you restored a full snapshot with secrets.',
  'Re-enter Hermes provider keys / sign in to providers (OPENCODE_ZEN_API_KEY etc.) unless the snapshot had secrets.',
  'Run claude login and codex login; reinstall the OpenAI Codex app (node_repl runtime lives there).',
  'Set Windows user variable HERMES_WRITE_SAFE_ROOT (shown by the restore above) and HERMES_WEBUI_PYTHON.',
  'Rebuild runtimes: python -m venv + pip install -r requirements.txt; scripts\hermes-webapp-component.py ensure (pins in config\hermes-component.lock.json).',
  'Re-apply the hermes-webui patch: integrations\hermes-mode\patches\webui-trusted-workspaces.patch.',
  'Scheduled tasks: re-run with -RestoreTasks, or import the XML in inventory\ yourself.',
  'Re-mint workspace access grants by signing in to Odysseus and opening Hermes (grants are intentionally not backed up).',
  'Reload MCP in Hermes once (Settings > Connectors) so Alyn, Wiki, REA and node_repl connect.') | ForEach-Object { Write-Host "  [ ] $_" }
if (-not $Apply) { Write-Host "`nDry run only. Add -Apply to restore." -ForegroundColor Yellow }

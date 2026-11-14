<#
.SYNOPSIS  Back up everything the built-in Odysseus backup does NOT cover (read-only on your live data).
.DESCRIPTION
 Makes a folder "recovery-<timestamp>" containing:
   git\*.bundle        every branch of each repository (unpublished work, worktrees' branches included)
   git\*-uncommitted.patch / *-untracked.zip   local edits that are not committed (.env* files are never zipped)
   inventory\          Windows user variables (secret-looking names skipped), scheduled-task XML, model file list, repo list
 It never stops, restarts or changes anything. Run odysseus-backup.py create (see RESTORE_GUIDE.md) for Odysseus + managed Hermes.
.EXAMPLE   .\backup-extras.ps1 -OutDir E:\Recovery
#>
param(
  [string]$OutDir = "$env:USERPROFILE\OdysseusRecovery",
  [string[]]$Repos = @('F:\Odysseus', 'F:\hermes-webui', 'F:\LongCat-Worktrees\odysseus-wincookbook', 'F:\Projects\Odysseus-Wiki', 'F:\hermes-personal-skills'),
  [string]$ModelDir,
  [switch]$Encrypt
)
$ErrorActionPreference = 'Continue'   # git writes progress to stderr; PowerShell 5.1 would treat that as fatal under 'Stop'
$stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
$dest = Join-Path $OutDir "recovery-$stamp"
foreach ($d in 'git','inventory') { New-Item -ItemType Directory (Join-Path $dest $d) -Force | Out-Null }
$summary = [ordered]@{ created = (Get-Date).ToString('o'); repos = @() }

foreach ($repo in $Repos) {
    if (-not (Test-Path (Join-Path $repo '.git'))) { Write-Warning "Skipping $repo (not a git repository)"; continue }
    $name = ($repo -replace '[:\\/ ]+', '_').Trim('_')
    $head = (git -C $repo rev-parse --short HEAD 2>$null)
    $row = [ordered]@{ path = $repo; head = $head; bundle = $null; uncommitted = $false; remotes = (git -C $repo remote -v) -join '; ' }
    if ($head) {
        git -C $repo bundle create (Join-Path $dest "git\$name.bundle") --all 2>&1 | Out-Null
        git -C $repo bundle verify (Join-Path $dest "git\$name.bundle") 2>&1 | Out-Null
        if ($LASTEXITCODE -eq 0) { $row.bundle = "git\$name.bundle" } else { Write-Warning "Bundle for $repo failed verification" }
    }
    git -C $repo branch -vv --all > (Join-Path $dest "git\$name-branches.txt")
    git -C $repo worktree list > (Join-Path $dest "git\$name-worktrees.txt")
    # uncommitted + untracked files of the main checkout AND of every linked worktree (bundles only hold commits)
    $trees = @($repo) + @(git -C $repo worktree list --porcelain | Where-Object { $_ -like 'worktree *' } | ForEach-Object { $_.Substring(9) -replace '/', '\' } | Where-Object { $_ -ne ($repo -replace '/', '\') -and (Test-Path $_) })
    foreach ($wt in $trees) {
        $wname = ($wt -replace '[:\\/ ]+', '_').Trim('_')
        $patch = git -C $wt diff HEAD
        if ($patch) { $patch | Set-Content (Join-Path $dest "git\$wname-uncommitted.patch") -Encoding UTF8; $row.uncommitted = $true }
        $untracked = @(git -C $wt ls-files --others --exclude-standard | Where-Object { $_ -notmatch '(^|[\\/])\.env' -and $_ -notmatch '\.(db|sqlite3?|pem|key|token)$' })
        if ($untracked.Count -gt 0 -and $untracked.Count -lt 5000) {
            $stage = Join-Path $env:TEMP "untracked-$stamp-$wname"; New-Item -ItemType Directory $stage -Force | Out-Null
            foreach ($u in $untracked) { $src = Join-Path $wt $u; if ((Test-Path $src -PathType Leaf) -and (Get-Item $src).Length -lt 5MB) { $t = Join-Path $stage $u; New-Item -ItemType Directory (Split-Path $t) -Force | Out-Null; Copy-Item $src $t } }
            Compress-Archive -Path (Join-Path $stage '*') -DestinationPath (Join-Path $dest "git\$wname-untracked.zip") -Force -ErrorAction SilentlyContinue
            Remove-Item $stage -Recurse -Force; $row.uncommitted = $true
        }
    }
    $summary.repos += $row
}

# Windows user variables: names + values, but never anything that looks like a secret
$vars = [Environment]::GetEnvironmentVariables('User')
$safe = [ordered]@{}; foreach ($k in $vars.Keys) { if ($k -notmatch 'KEY|TOKEN|SECRET|PASS|CREDENTIAL|COOKIE') { $safe[$k] = $vars[$k] } }
$safe | ConvertTo-Json | Set-Content (Join-Path $dest 'inventory\user-environment.json') -Encoding UTF8
foreach ($task in 'Odysseus AutoStart','Hermes_Gateway') {
    try { Export-ScheduledTask -TaskName $task | Set-Content (Join-Path $dest "inventory\task-$($task -replace ' ','_').xml") -Encoding UTF8 } catch { Write-Warning "Task $task not found" }
}
if (-not $ModelDir -and (Test-Path 'F:\Odysseus\.env')) {
    $line = Select-String -Path 'F:\Odysseus\.env' -Pattern '^\s*HF_HOME\s*=\s*(.+)$' | Select-Object -First 1
    if ($line) { $ModelDir = $line.Matches[0].Groups[1].Value.Trim() }
}
if ($ModelDir -and (Test-Path $ModelDir)) {
    Get-ChildItem $ModelDir -Recurse -File -ErrorAction SilentlyContinue | Where-Object { $_.Extension -in '.gguf','.safetensors','.onnx','.bin','.pt' } |
        Select-Object @{n='path';e={$_.FullName}}, @{n='MB';e={[math]::Round($_.Length/1MB,1)}} | Export-Csv (Join-Path $dest 'inventory\model-files.csv') -NoTypeInformation
}
$summary | ConvertTo-Json -Depth 4 | Set-Content (Join-Path $dest 'recovery-manifest.json') -Encoding UTF8
Write-Host "Recovery bundle: $dest"
if ($Encrypt) {
    $c7 = Get-Command 7z -ErrorAction SilentlyContinue; $7z = if ($c7) { $c7.Source }
    if (-not $7z) { Write-Warning '7-Zip not found: encrypt the folder yourself (see RESTORE_GUIDE.md).' }
    else { & $7z a -t7z -mhe=on -p "$dest.7z" $dest; Write-Host "Encrypted archive: $dest.7z (you typed the passphrase; keep it OFF this PC)" }
}

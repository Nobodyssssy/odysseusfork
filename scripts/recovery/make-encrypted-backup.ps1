<#
.SYNOPSIS  One-step, encrypted, FULL disaster-recovery backup (includes credentials). Read-only on live data.
.DESCRIPTION
 1. Creates a full Odysseus + Hermes snapshot (secrets included) and, with -IncludeExtras, the git bundles / inventory.
 2. Packs it into one tar and encrypts it with GnuPG symmetric AES-256 (passphrase you type; asked twice; 16+ characters).
 3. Decrypts the result again and compares hashes (integrity proof).
 4. Deletes every unencrypted temporary copy.
 The passphrase is never written to disk, printed or logged. Nobody else (including any assistant) ever sees it.
 -Destination must be on a drive that is NOT the disk you will reinstall (a USB disk is best). Nothing is changed in production.
.EXAMPLE
 .\make-encrypted-backup.ps1 -Destination U:\OdysseusVault -IncludeExtras
.TO RESTORE
 gpg --output backup.tar --decrypt odysseus-recovery-YYYYMMDD-HHMMSS.tar.gpg      (asks for the passphrase)
 tar -xf backup.tar          -> folders snapshot-* and recovery-*; then see RESTORE_GUIDE.md section 3
#>
param(
  [Parameter(Mandatory)][string]$Destination,
  [string]$OdysseusRoot, [string]$DataDir, [string]$HermesHome, [string]$ComponentRoot, [string]$StartupDir,
  [switch]$IncludeExtras,
  [string]$PassphraseEnvVar        # automation/testing only: name of an environment variable holding the passphrase
)
$ErrorActionPreference = 'Continue'
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
if (-not $OdysseusRoot) { $OdysseusRoot = Split-Path -Parent (Split-Path -Parent $here) }
$cli = (Resolve-Path (Join-Path $here '..\odysseus-backup.py')).Path   # the backup tool that ships next to this script
if (-not $DataDir) { $DataDir = Join-Path $OdysseusRoot 'data' }
$gpg = @('C:\Program Files\Git\usr\bin\gpg.exe', (Get-Command gpg -ErrorAction SilentlyContinue | ForEach-Object { $_.Source })) | Where-Object { $_ -and (Test-Path $_) } | Select-Object -First 1
if (-not $gpg) { throw 'GnuPG not found (it ships with Git for Windows). Install Git for Windows and retry.' }
if (-not (Get-Command python -ErrorAction SilentlyContinue)) { throw 'python not found.' }
New-Item -ItemType Directory $Destination -Force | Out-Null
$destRoot = (Get-Item $Destination).PSDrive.Name
if ($destRoot -eq $env:SystemDrive.TrimEnd(':')) { Write-Warning "Destination is on the system drive ($env:SystemDrive). That disk is wiped when you reinstall Windows: copy the .gpg file to another disk afterwards." }

# passphrase: typed by the user, kept only in memory
if ($PassphraseEnvVar) { $pass = [Environment]::GetEnvironmentVariable($PassphraseEnvVar) }
else {
  $s1 = Read-Host 'Choose an encryption passphrase (16+ characters, unique, store it in a password manager)' -AsSecureString
  $s2 = Read-Host 'Repeat the passphrase' -AsSecureString
  $b1 = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($s1); $b2 = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($s2)
  $pass = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($b1); $pass2 = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($b2)
  [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($b1); [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($b2)
  if ($pass -cne $pass2) { throw 'The two passphrases differ. Nothing was created.' }
}
if (-not $pass -or $pass.Length -lt 16) { throw 'Passphrase must be at least 16 characters. Nothing was created.' }

$stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
$stage = Join-Path $Destination ".staging-$stamp"
New-Item -ItemType Directory $stage -Force | Out-Null
$tar = Join-Path $stage 'bundle.tar'; $out = Join-Path $Destination "odysseus-recovery-$stamp.tar.gpg"; $chk = Join-Path $stage 'verify.tar'
try {
  $a = @($cli, '--odysseus-root', $OdysseusRoot, '--data-dir', $DataDir, '--backup-dir', $stage)
  if ($HermesHome) { $a += @('--hermes-home', $HermesHome) }; if ($ComponentRoot) { $a += @('--component-root', $ComponentRoot) }; if ($StartupDir) { $a += @('--startup-dir', $StartupDir) }
  $a += 'create'
  Write-Host 'Creating full snapshot (secrets included)...'
  python @a; if ($LASTEXITCODE -ne 0) { throw 'Snapshot creation failed.' }
  $snap = Get-ChildItem $stage -Directory -Filter 'snapshot-*' | Select-Object -First 1
  python $cli --backup-dir $stage verify $snap.Name; if ($LASTEXITCODE -ne 0) { throw 'Snapshot verification failed.' }
  if ($IncludeExtras) { Write-Host 'Adding git bundles and inventory...'; & (Join-Path $here 'backup-extras.ps1') -OutDir $stage | Out-Null }
  $items = @(Get-ChildItem $stage -Directory | ForEach-Object { $_.Name })
  & "$env:SystemRoot\System32\tar.exe" -cf $tar -C $stage @items; if ($LASTEXITCODE -ne 0) { throw 'tar failed.' }
  $h1 = (Get-FileHash $tar -Algorithm SHA256).Hash
  Write-Host 'Encrypting (AES-256)...'
  $pass | & $gpg --batch --yes --pinentry-mode loopback --passphrase-fd 0 --symmetric --cipher-algo AES256 --s2k-mode 3 --s2k-digest-algo SHA512 --s2k-count 65011712 --output $out $tar 2>&1 | Out-Null
  if (-not (Test-Path $out)) { throw 'Encryption failed.' }
  Write-Host 'Verifying by decrypting again...'
  $pass | & $gpg --batch --yes --pinentry-mode loopback --passphrase-fd 0 --decrypt --output $chk $out 2>&1 | Out-Null
  if (-not (Test-Path $chk) -or (Get-FileHash $chk -Algorithm SHA256).Hash -ne $h1) { Remove-Item $out -Force -ErrorAction SilentlyContinue; throw 'Decrypt check FAILED. The encrypted file was removed.' }
  $enc = (Get-FileHash $out -Algorithm SHA256).Hash
  @("Encrypted recovery backup", "File: $(Split-Path $out -Leaf)", "Created: $((Get-Date).ToString('o'))", "SHA256 of the .gpg file: $enc", "Snapshot inside: $($snap.Name)", "Extras included: $([bool]$IncludeExtras)", "Cipher: GnuPG symmetric AES-256, SHA-512 S2K x 65011712", "Passphrase: NOT stored anywhere. Keep it in your password manager.") | Set-Content (Join-Path $Destination "odysseus-recovery-$stamp.txt") -Encoding UTF8
  Write-Host "OK: $out" -ForegroundColor Green
  Write-Host "SHA256: $enc"
} finally {
  $pass = $null
  if (Test-Path $stage) { Remove-Item $stage -Recurse -Force -ErrorAction SilentlyContinue }
  if (Test-Path $stage) { Write-Warning "Could not delete ${stage}: it holds UNENCRYPTED data. Delete it yourself." } else { Write-Host 'Temporary unencrypted copies deleted.' }
}

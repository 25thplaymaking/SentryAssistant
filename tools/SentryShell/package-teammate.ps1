<#
.SYNOPSIS
  Build a distributable Frontir Sentry package for one teammate.

.DESCRIPTION
  Produces a self-contained folder + zip the teammate unzips and runs. It:

    1. publishes the shell (Release, win-x64),
    2. generates a dedicated SSH keypair for that teammate,
    3. writes sentry.json so the app dials THEIR account, not the owner's, and
       forwards only the WebUI port,
    4. authorises the public key on the server, restricted to forwarding
       127.0.0.1:8787 and nothing else (no shell, no other ports),
    5. writes a short README and zips the lot.

  The teammate still needs an ENROLLMENT CODE to sign in. Codes expire in five
  minutes, so they are deliberately NOT baked into the package -- mint one when
  they are at the keyboard:

      ssh <server> "cd /srv/sentry/repo/deploy/linux && ./provision-teammate.sh --name 'Alice' --slug alice"

.PARAMETER Slug
  Short identifier, e.g. alice. Names the key, the config and the zip.

.PARAMETER DisplayName
  Human name shown in the app's window title.

.PARAMETER Server
  SSH host of the Sentry server. Defaults to the owner's.

.PARAMETER SshUser
  Account the teammate's restricted key is added to. Defaults to the owner's
  account -- their key is restricted per-key, so this grants forwarding only.

.PARAMETER SkipAuthorize
  Build the package but do NOT touch the server. Prints the command to run later.

.EXAMPLE
  .\package-teammate.ps1 -Slug alice -DisplayName "Alice"
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$Slug,
    [string]$DisplayName = "",
    [string]$Server  = "205.209.116.114",
    [string]$SshUser = "bishop",
    [switch]$SkipAuthorize
)

$ErrorActionPreference = 'Stop'
if ($Slug -notmatch '^[a-z0-9][a-z0-9-]{0,30}$') {
    throw "Slug must match ^[a-z0-9][a-z0-9-]{0,30}$ (got '$Slug')"
}
if (-not $DisplayName) { $DisplayName = $Slug }

$ProjectDir = $PSScriptRoot
$OutRoot    = Join-Path $ProjectDir 'dist-packages'
$PkgDir     = Join-Path $OutRoot "FrontirSentry-$Slug"
$Zip        = Join-Path $OutRoot "FrontirSentry-$Slug.zip"

function Step($m) { Write-Host "==> $m" -ForegroundColor Cyan }
function Ok($m)   { Write-Host "    $m" -ForegroundColor Green }

# 1. Publish -----------------------------------------------------------------
Step 'Publishing the shell'
$dotnet = 'C:\Program Files\dotnet\dotnet.exe'
if (-not (Test-Path $dotnet)) { $dotnet = 'dotnet' }
if (Test-Path $PkgDir) { Remove-Item $PkgDir -Recurse -Force }
New-Item -ItemType Directory -Force -Path $PkgDir | Out-Null
& $dotnet publish (Join-Path $ProjectDir 'SentryShell.csproj') `
    -c Release -r win-x64 --self-contained false -o $PkgDir --nologo -v quiet
if ($LASTEXITCODE -ne 0) { throw "publish failed ($LASTEXITCODE)" }

$exe = Join-Path $PkgDir 'FrontirSentry.exe'
if (-not (Test-Path $exe)) { throw "publish produced no FrontirSentry.exe" }
# A console-subsystem binary would flash a window on every launch and reconnect.
$bytes = [IO.File]::ReadAllBytes($exe)
$sub = [BitConverter]::ToUInt16($bytes, [BitConverter]::ToInt32($bytes, 0x3c) + 0x5c)
if ($sub -ne 2) { throw "FrontirSentry.exe is subsystem $sub; expected 2 (Windows GUI)" }
Ok 'published, subsystem 2 verified'

# 2. Keypair -----------------------------------------------------------------
Step 'Generating a dedicated SSH key'
$keyPath = Join-Path $PkgDir "sentry-$Slug"
if (Test-Path $keyPath) { Remove-Item $keyPath, "$keyPath.pub" -Force -ErrorAction SilentlyContinue }
# No passphrase: the app dials unattended at logon. The key's power is bounded
# by the server-side restriction (one forwarded port, no shell), not by a
# passphrase the user would have to type on every reconnect.
& ssh-keygen -t ed25519 -N '""' -C "sentry-$Slug" -f $keyPath -q
if (-not (Test-Path "$keyPath.pub")) { throw "ssh-keygen produced no key" }
$pub = (Get-Content "$keyPath.pub" -Raw).Trim()
Ok "keypair generated (sentry-$Slug)"

# 3. Config ------------------------------------------------------------------
Step 'Writing sentry.json'
[ordered]@{
    sshTarget      = "$SshUser@$Server"
    identityFile   = "sentry-$Slug"   # resolved next to the exe at runtime
    forwardGateway = $false           # teammates get the WebUI port only
    displayName    = $DisplayName
} | ConvertTo-Json | Set-Content (Join-Path $PkgDir 'sentry.json') -Encoding UTF8
Ok 'configured for the WebUI port only'

# 4. Authorise on the server -------------------------------------------------
if ($SkipAuthorize) {
    Write-Host "    SKIPPED (-SkipAuthorize). Run on the server:" -ForegroundColor Yellow
    Write-Host "      cd /srv/sentry/repo/deploy/linux && ./authorize-tunnel-key.sh --slug $Slug --pubkey '$pub'"
} else {
    Step 'Authorising the key on the server (forwarding only)'
    $remote = "cd /srv/sentry/repo/deploy/linux && ./authorize-tunnel-key.sh --slug '$Slug' --pubkey '$pub'"
    & ssh -o BatchMode=yes "$SshUser@$Server" $remote
    if ($LASTEXITCODE -ne 0) { throw "could not authorise the key on $Server" }
    Ok 'authorised'
}

# 5. README + zip ------------------------------------------------------------
Step 'Writing README and zipping'
@"
Frontir Sentry — $DisplayName
=============================

WHAT THIS IS
  Your own Sentry assistant. It runs in one window and manages its own secure
  connection to the server; there is no console, no terminal and nothing to
  configure.

FIRST RUN
  1. Keep every file in this folder together — the app reads sentry.json and
     the key file beside it.
  2. Double-click FrontirSentry.exe.
  3. On the sign-in page, paste the ENROLLMENT CODE you were given.
     Codes expire five minutes after they are issued; ask for a fresh one if
     yours has lapsed.

AFTER THAT
  You stay signed in on this machine. Closing the window with X leaves it
  running in the system tray (bottom-right, near the clock); use Quit in the
  tray menu to exit properly.

TO START AUTOMATICALLY AT LOGIN
  Press Win+R, type  shell:startup  , press Enter, and drop a shortcut to
  FrontirSentry.exe into the folder that opens.

FILES
  FrontirSentry.exe   the app
  sentry.json         which server to reach — do not edit
  sentry-$Slug        your private key. Treat it like a password.
                      It can ONLY open the Sentry connection: it cannot log
                      into the server or run commands there.

IF IT SAYS IT CANNOT CONNECT
  The server may be down or your key may not be authorised yet. Send whoever
  gave you this package the last few lines of:
      %LOCALAPPDATA%\SentryAssistant\shell.log
"@ | Set-Content (Join-Path $PkgDir 'README.txt') -Encoding UTF8

if (Test-Path $Zip) { Remove-Item $Zip -Force }
Compress-Archive -Path (Join-Path $PkgDir '*') -DestinationPath $Zip
Ok "package: $Zip"

Write-Host ""
Write-Host "Done. Send $([IO.Path]::GetFileName($Zip)) to $DisplayName over a channel you trust" -ForegroundColor Green
Write-Host "(it contains their private key), then mint their enrollment code:" -ForegroundColor Green
Write-Host "  ssh $SshUser@$Server `"cd /srv/sentry/repo/deploy/linux && ./provision-teammate.sh --name '$DisplayName' --slug $Slug`"" -ForegroundColor White

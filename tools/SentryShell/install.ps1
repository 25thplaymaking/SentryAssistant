<#
.SYNOPSIS
  Install Frontir Sentry as the desktop shell and retire the PowerShell tunnel.

.DESCRIPTION
  The Gateway and WebUI bind loopback-only on the server, so the desktop needs an
  SSH forward. That used to be a Scheduled Task running powershell.exe, which
  allocates a console *before* -WindowStyle Hidden applies -- so every logon and
  every reconnect flashed a window. FrontirSentry.exe is a WinExe (subsystem 2:
  never allocates a console) that owns the forward in-process and starts ssh with
  CreateNoWindow, so nothing is ever visible.

  This script is idempotent: publish -> install autostart -> retire the legacy
  task -> verify. Run it again after pulling changes to refresh the binary.

.PARAMETER SkipPublish
  Reuse the existing dist/ build instead of rebuilding.

.PARAMETER Uninstall
  Remove autostart and stop the app. Does NOT re-enable the legacy task.
#>
[CmdletBinding()]
param(
    [switch]$SkipPublish,
    [switch]$Uninstall
)

$ErrorActionPreference = 'Stop'
$ProjectDir = $PSScriptRoot
$Dist       = Join-Path $ProjectDir 'dist'
$BuildExe   = Join-Path $Dist 'FrontirSentry.exe'
# Installed OUT of the repo: AppPaths documents %LOCALAPPDATA%\SentryAssistant\app
# as the ship location, and running from a worktree would break on a branch
# switch or `git clean`.
$InstallDir = Join-Path ([Environment]::GetFolderPath('LocalApplicationData')) 'SentryAssistant\app'
$Exe        = Join-Path $InstallDir 'FrontirSentry.exe'
$ShellLog   = Join-Path ([Environment]::GetFolderPath('LocalApplicationData')) 'SentryAssistant\shell.log'
$Shortcut   = Join-Path ([Environment]::GetFolderPath('Startup')) 'Frontir Sentry.lnk'
$LegacyTask = 'SentryGatewayTunnel'

function Write-Step([string]$m) { Write-Host "==> $m" -ForegroundColor Cyan }
function Write-Ok  ([string]$m) { Write-Host "    $m" -ForegroundColor Green }
function Write-Warn([string]$m) { Write-Host "    $m" -ForegroundColor Yellow }

function Stop-Shell {
    Get-Process FrontirSentry -ErrorAction SilentlyContinue | ForEach-Object {
        Stop-Process -Id $_.Id -Force
        Write-Ok "stopped running shell (pid $($_.Id))"
    }
    # The tunnel's job object kills its ssh with it, but a shell killed
    # mid-start can outlive that; reap any forward matching our ports.
    Start-Sleep -Seconds 2
    Get-CimInstance Win32_Process -Filter "Name='ssh.exe'" |
        Where-Object { $_.CommandLine -match '\[::1\]:8090:127\.0\.0\.1:8090' } |
        ForEach-Object { Stop-Process -Id $_.ProcessId -Force; Write-Ok "reaped ssh pid $($_.ProcessId)" }
}

if ($Uninstall) {
    Write-Step 'Uninstalling'
    if (Test-Path $Shortcut) { Remove-Item $Shortcut -Force; Write-Ok 'autostart shortcut removed' }
    else { Write-Warn 'no autostart shortcut found' }
    Stop-Shell
    Write-Host "`nDone. The legacy task was left disabled deliberately -- re-enable it" -ForegroundColor White
    Write-Host "manually if you want the old PowerShell tunnel back." -ForegroundColor White
    return
}

# 1. Build ------------------------------------------------------------------
if (-not $SkipPublish) {
    Write-Step 'Publishing FrontirSentry (Release, win-x64)'
    # The `dotnet` first on PATH is an x86 install without the required SDK.
    $dotnet = 'C:\Program Files\dotnet\dotnet.exe'
    if (-not (Test-Path $dotnet)) { $dotnet = 'dotnet' }
    & $dotnet publish (Join-Path $ProjectDir 'SentryShell.csproj') `
        -c Release -r win-x64 --self-contained false -o $Dist --nologo -v quiet
    if ($LASTEXITCODE -ne 0) { throw "publish failed with exit code $LASTEXITCODE" }
    Write-Ok "published -> $Dist"
}
if (-not (Test-Path $BuildExe)) { throw "missing $BuildExe -- run without -SkipPublish" }

# Copy the build out of the repo into the ship location.
Write-Step "Installing to $InstallDir"
Stop-Shell   # cannot overwrite a running exe
New-Item -ItemType Directory -Force -Path $InstallDir | Out-Null
Copy-Item (Join-Path $Dist '*') $InstallDir -Recurse -Force
Write-Ok "installed -> $InstallDir"

# A console-subsystem binary would flash a window; that is the whole point of
# this project, so verify it rather than trusting the csproj.
$bytes = [IO.File]::ReadAllBytes($Exe)
$peOff = [BitConverter]::ToInt32($bytes, 0x3c)
$subsystem = [BitConverter]::ToUInt16($bytes, $peOff + 0x5c)
if ($subsystem -ne 2) { throw "FrontirSentry.exe is subsystem $subsystem; expected 2 (Windows GUI, no console)" }
Write-Ok 'verified subsystem 2 (Windows GUI - never allocates a console)'

# 2. Retire the legacy PowerShell tunnel -------------------------------------
Write-Step 'Retiring the legacy PowerShell scheduled task'
$task = Get-ScheduledTask -TaskName $LegacyTask -ErrorAction SilentlyContinue
if ($task) {
    if ($task.State -ne 'Disabled') {
        Stop-ScheduledTask  -TaskName $LegacyTask -ErrorAction SilentlyContinue
        Disable-ScheduledTask -TaskName $LegacyTask | Out-Null
        Write-Ok "$LegacyTask disabled"
    } else {
        Write-Ok "$LegacyTask already disabled"
    }
    # ExitOnForwardFailure means a leftover forward on 8787/8090 makes the
    # shell's own dial fail outright, so clear it before starting.
    # Match on the FORWARDED PORTS, not on the host. Filtering by "not a child of
    # FrontirSentry" is wrong when the shell isn't running: the comparison value
    # is $null, every ssh matches, and this kills the operator's own interactive
    # session to the same box.
    Get-CimInstance Win32_Process -Filter "Name='ssh.exe'" |
        Where-Object {
            $_.CommandLine -match '\[::1\]:8090:127\.0\.0\.1:8090' -or
            $_.CommandLine -match '\[::1\]:8787:127\.0\.0\.1:8787'
        } |
        ForEach-Object { Stop-Process -Id $_.ProcessId -Force; Write-Ok "cleared stale forward pid $($_.ProcessId)" }
} else {
    Write-Ok 'no legacy task present'
}

# 3. Autostart ---------------------------------------------------------------
Write-Step 'Installing autostart (Startup folder, not Task Scheduler)'
$w = New-Object -ComObject WScript.Shell
$s = $w.CreateShortcut($Shortcut)
$s.TargetPath       = $Exe
$s.WorkingDirectory = $InstallDir
$s.Description      = 'Frontir Sentry - desktop shell, owns its own Gateway tunnel'
$s.Save()
Write-Ok "shortcut -> $Shortcut"

# 4. Start + verify ----------------------------------------------------------
Write-Step 'Starting the shell'
Stop-Shell
Start-Process -FilePath $Exe
Write-Ok 'launched; waiting for the tunnel to come up'

$deadline = (Get-Date).AddSeconds(60)
$gateway = $webui = 0
while ((Get-Date) -lt $deadline) {
    Start-Sleep -Seconds 5
    try { $gateway = (Invoke-WebRequest "http://[::1]:8090/health/ready" -TimeoutSec 5 -UseBasicParsing).StatusCode } catch { $gateway = 0 }
    try { $webui   = (Invoke-WebRequest "http://[::1]:8787/login"        -TimeoutSec 5 -UseBasicParsing).StatusCode } catch { $webui = 0 }
    if ($gateway -eq 200 -and $webui -eq 200) { break }
}

Write-Step 'Result'
Write-Host ("    Gateway [::1]:8090  -> {0}" -f $(if ($gateway -eq 200) { 'ready' } else { "UNREACHABLE ($gateway)" }))
Write-Host ("    WebUI   [::1]:8787  -> {0}" -f $(if ($webui   -eq 200) { 'ready' } else { "UNREACHABLE ($webui)" }))
if ($gateway -eq 200 -and $webui -eq 200) {
    Write-Host "`nFrontir Sentry owns its own connection. No PowerShell, no console, no Task Scheduler." -ForegroundColor Green
    Write-Host "It starts automatically at logon and closes to the system tray." -ForegroundColor Green
} else {
    Write-Host "`nThe shell is running but a layer is unreachable." -ForegroundColor Yellow
    Write-Host "Check the shell log: $ShellLog" -ForegroundColor Yellow
    exit 1
}

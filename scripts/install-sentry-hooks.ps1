<#
.SYNOPSIS
    Adds Sentry's hooks to Claude Code without disturbing existing ones.

.DESCRIPTION
    A thin wrapper. The merge, the backup, and the refusal to touch an
    unparseable settings file all live in the Sentry node binary, where they are
    covered by tests — a copy of that logic written in PowerShell would be a
    second implementation that nothing verifies.

    Installing is additive and idempotent. Hooks already registered, including
    claude-presence, are left exactly as they are, and running this twice
    changes nothing the second time.

.PARAMETER Uninstall
    Remove only Sentry's own entries.

.PARAMETER SettingsPath
    Defaults to ~/.claude/settings.json.

.EXAMPLE
    ./install-sentry-hooks.ps1
    ./install-sentry-hooks.ps1 -Uninstall
#>
[CmdletBinding()]
param(
    [switch]$Uninstall,
    [string]$SettingsPath
)

$ErrorActionPreference = 'Stop'

$repo = Split-Path -Parent $PSScriptRoot
$project = Join-Path $repo 'src\Sentry.Node.Host\Sentry.Node.Host.csproj'
if (-not (Test-Path $project)) { throw "Cannot find $project" }

# The x64 SDK explicitly: the dotnet first on PATH here is an x86 install
# without it.
$dotnet = 'C:\Program Files\dotnet\dotnet.exe'
if (-not (Test-Path $dotnet)) { $dotnet = 'dotnet' }

$verb = if ($Uninstall) { 'uninstall-hooks' } else { 'install-hooks' }

$arguments = @('run', '--project', $project, '-c', 'Release', '--', $verb)
if ($SettingsPath) { $arguments += @('--settings', $SettingsPath) }

Write-Host "Running $verb..." -ForegroundColor Cyan
& $dotnet @arguments
exit $LASTEXITCODE

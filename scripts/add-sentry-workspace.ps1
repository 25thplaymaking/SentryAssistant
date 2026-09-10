[CmdletBinding()]
param(
    [Parameter(Mandatory = $true, Position = 0)]
    [string]$Path,
    [Parameter(Mandatory = $false, Position = 1)]
    [string]$Id = "",
    [ValidateSet("workspaceWrite", "readOnly")]
    [string]$Mode = "workspaceWrite"
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$cleanPath = $Path.Trim('"', "'")
if (-not (Test-Path -LiteralPath $cleanPath -PathType Container)) {
    throw "Directory does not exist: $cleanPath"
}
$fullPath = (Get-Item -LiteralPath $cleanPath).FullName

if ([string]::IsNullOrWhiteSpace($Id)) {
    $folderName = Split-Path $fullPath -Leaf
    $Id = [regex]::Replace($folderName.ToLowerInvariant(), '[^a-z0-9]+', '-').Trim('-')
}
if ([string]::IsNullOrWhiteSpace($Id)) {
    throw "Could not determine a valid workspace ID from path '$fullPath'. Specify -Id explicitly."
}

$stateRoot = Join-Path $env:LOCALAPPDATA "SentryAssistant\node\state"
$configPath = Join-Path $stateRoot "appsettings.node.json"

if (-not (Test-Path -LiteralPath $configPath)) {
    throw "Sentry node configuration not found at $configPath"
}

$json = Get-Content -LiteralPath $configPath -Raw | ConvertFrom-Json
$workspaces = @($json.workspaces)

foreach ($ws in $workspaces) {
    if ($ws.workspaceId -eq $Id) {
        Write-Warning "Workspace '$Id' already exists pointing to '$($ws.rootPath)'. Updating path..."
        $ws.rootPath = $fullPath
        $json.workspaces = $workspaces
        $json | ConvertTo-Json -Depth 10 | Set-Content -LiteralPath $configPath -Encoding utf8
        Write-Host "Updated workspace '$Id' -> '$fullPath'" -ForegroundColor Green
        return
    }
}

$modes = if ($Mode -eq "readOnly") { @("readOnly") } else { @("readOnly", "workspaceWrite") }
$newWs = [PSCustomObject]@{
    workspaceId = $Id
    rootPath = $fullPath
    allowedHarnesses = @("shell", "claude", "codex")
    allowedModes = $modes
}

$workspaces += $newWs
$json.workspaces = $workspaces
$json | ConvertTo-Json -Depth 10 | Set-Content -LiteralPath $configPath -Encoding utf8

Write-Host "Successfully registered workspace '$Id' -> '$fullPath'" -ForegroundColor Green
Write-Host "The Sentry execution node will automatically reload and publish this workspace to the Gateway." -ForegroundColor Cyan

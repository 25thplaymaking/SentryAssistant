[CmdletBinding()]
param(
    [string]$SshTarget = "bishop@205.209.116.114",
    [string]$GatewayUrl = "http://[::1]:8090",
    [string]$DisplayName = "Bryce",
    [string]$NodeName = "Bryce's PC",
    [string]$TaskName = "Frontir Sentry Execution Node",
    [string]$RequiredSdkVersion = "10.0.301",
    [string]$ClaudeExecutable = "$env:USERPROFILE\.local\bin\claude.exe"
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

function Invoke-PrivateSsh {
    param([string]$Command)
    $ssh = (Get-Command ssh.exe -ErrorAction Stop).Source
    $output = & $ssh -o BatchMode=yes -o StrictHostKeyChecking=accept-new `
        -o ConnectTimeout=10 $SshTarget $Command 2>&1
    if ($LASTEXITCODE -ne 0) {
        throw "The Sentry server did not complete a required private setup step."
    }
    return ($output -join "`n")
}

function Get-JwtSubject {
    param([string]$Token)
    $parts = $Token.Split('.')
    if ($parts.Count -ne 3) { throw "The Gateway returned an invalid access token." }
    $payload = $parts[1].Replace('-', '+').Replace('_', '/')
    switch ($payload.Length % 4) {
        2 { $payload += '==' }
        3 { $payload += '=' }
        1 { throw "The Gateway returned an invalid access token." }
    }
    $json = [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String($payload)) |
        ConvertFrom-Json
    if ([string]::IsNullOrWhiteSpace([string]$json.sub)) {
        throw "The Gateway token has no owner identity."
    }
    return [string]$json.sub
}

function Protect-ForCurrentUser {
    param([string]$Value, [byte[]]$Entropy)
    $bytes = [Text.Encoding]::UTF8.GetBytes($Value)
    $protected = [Security.Cryptography.ProtectedData]::Protect(
        $bytes, $Entropy, [Security.Cryptography.DataProtectionScope]::CurrentUser)
    return [Convert]::ToBase64String($protected)
}

function Resolve-Dotnet {
    param([string]$InstallRoot)
    $systemDotnet = "C:\Program Files\dotnet\dotnet.exe"
    if (Test-Path -LiteralPath $systemDotnet) {
        $sdks = & $systemDotnet --list-sdks
        if ($sdks -match "(?m)^$([regex]::Escape($RequiredSdkVersion)) \[") {
            return $systemDotnet
        }
    }

    $sdkRoot = Join-Path $InstallRoot "sdk"
    $privateDotnet = Join-Path $sdkRoot "dotnet.exe"
    if (-not (Test-Path -LiteralPath (Join-Path $sdkRoot "sdk\$RequiredSdkVersion"))) {
        New-Item -ItemType Directory -Force -Path $sdkRoot | Out-Null
        $installer = Join-Path ([IO.Path]::GetTempPath()) "sentry-dotnet-install.ps1"
        Invoke-WebRequest "https://dot.net/v1/dotnet-install.ps1" -OutFile $installer
        & powershell.exe -NoLogo -NoProfile -NonInteractive -ExecutionPolicy Bypass `
            -File $installer -Version $RequiredSdkVersion -InstallDir $sdkRoot -NoPath
        if ($LASTEXITCODE -ne 0) { throw "The required .NET SDK could not be installed." }
    }
    if (-not (Test-Path -LiteralPath $privateDotnet)) {
        throw "The private .NET SDK installation is incomplete."
    }
    return $privateDotnet
}

if ($SshTarget -notmatch '^[A-Za-z0-9_.@:\-]+$') {
    throw "SSH target contains unsupported characters."
}
if ($DisplayName -notmatch '^[A-Za-z0-9 _.\-]+$') {
    throw "Display name contains unsupported characters."
}

$repoRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot ".."))
$hostProject = Join-Path $repoRoot "src\Sentry.Node.Host\Sentry.Node.Host.csproj"
$allowedRoot = [IO.Path]::GetFullPath((Join-Path $env:LOCALAPPDATA "SentryAssistant"))
$installRoot = [IO.Path]::GetFullPath((Join-Path $allowedRoot "node"))
$allowedPrefix = $allowedRoot.TrimEnd('\') + '\'
if (-not $installRoot.StartsWith($allowedPrefix, [StringComparison]::OrdinalIgnoreCase)) {
    throw "The node install directory resolved outside SentryAssistant's local data."
}

$serverWork = [IO.Path]::GetFullPath((Join-Path $env:USERPROFILE "Documents\ServerWork"))
$enfusion = [IO.Path]::GetFullPath((Join-Path $env:USERPROFILE "Documents\Enfusion"))
foreach ($workspace in @($serverWork, $enfusion)) {
    if (-not (Test-Path -LiteralPath $workspace -PathType Container)) {
        throw "A configured Sentry workspace does not exist: $workspace"
    }
}
$claudePath = [IO.Path]::GetFullPath($ClaudeExecutable)
if (-not (Test-Path -LiteralPath $claudePath -PathType Leaf)) {
    throw "Claude Code is not installed at the configured location."
}

New-Item -ItemType Directory -Force -Path $installRoot | Out-Null
$stateRoot = Join-Path $installRoot "state"
New-Item -ItemType Directory -Force -Path $stateRoot | Out-Null
$dotnet = Resolve-Dotnet $installRoot

$revision = (& git -C $repoRoot rev-parse --short HEAD).Trim()
if ($LASTEXITCODE -ne 0 -or $revision -notmatch '^[0-9a-f]+$') {
    throw "The Sentry source revision could not be identified."
}
$releaseRoot = [IO.Path]::GetFullPath((Join-Path $installRoot "releases\$revision"))
New-Item -ItemType Directory -Force -Path $releaseRoot | Out-Null

& $dotnet publish $hostProject -c Release -r win-x64 --self-contained true `
    -p:SentryNodeHeadless=true -o $releaseRoot
if ($LASTEXITCODE -ne 0) { throw "The Sentry workstation node could not be built." }
$nodeExecutable = Join-Path $releaseRoot "sentry-node.exe"
if (-not (Test-Path -LiteralPath $nodeExecutable -PathType Leaf)) {
    throw "The published Sentry node executable is missing."
}

# Mint the one-use execution-node enrollment privately over the existing SSH
# trust. The code, token pair, and signing key are captured in memory only.
$bootstrapCommand = "docker exec sentry-gateway-1 python3 scripts/bootstrap_device.py --display-name '$DisplayName' --device-kind executionNode"
$bootstrap = Invoke-PrivateSsh $bootstrapCommand
$codeMatch = [regex]::Match($bootstrap, '(?im)enrollment code:\s*([0-9A-F]{4}(?:-[0-9A-F]{4}){2})')
if (-not $codeMatch.Success) { throw "The server did not issue an execution-node enrollment." }
$enrollmentCode = $codeMatch.Groups[1].Value
$signingKey = (Invoke-PrivateSsh "docker exec sentry-gateway-1 printenv SENTRY_SIGNING_KEY").Trim()
if ([Text.Encoding]::UTF8.GetByteCount($signingKey) -lt 32) {
    throw "The server signing key was unavailable."
}

$tokens = Invoke-RestMethod -Method Post -Uri "$GatewayUrl/api/auth/enroll/complete" `
    -ContentType "application/json" -Body (@{
        code = $enrollmentCode
        device_name = $NodeName
    } | ConvertTo-Json -Compress)
$ownerUserId = Get-JwtSubject ([string]$tokens.access_token)

$workspaceRegistration = @(
    @{
        workspace_id = "server-work"
        allowed_harnesses = @("shell", "claude")
        allowed_modes = @("readOnly", "workspaceWrite")
    },
    @{
        workspace_id = "enfusion"
        allowed_harnesses = @("shell", "claude")
        allowed_modes = @("readOnly", "workspaceWrite")
    }
)
$registration = Invoke-RestMethod -Method Post -Uri "$GatewayUrl/api/nodes/register" `
    -Headers @{ Authorization = "Bearer $($tokens.access_token)" } `
    -ContentType "application/json" -Body (@{
        name = $NodeName
        workspaces = $workspaceRegistration
    } | ConvertTo-Json -Depth 6 -Compress)

$credentialPath = Join-Path $stateRoot "node.credentials.json"
$logPath = Join-Path $stateRoot "node.log"
$configPath = Join-Path $stateRoot "appsettings.node.json"
$entropy = [Text.Encoding]::UTF8.GetBytes("Frontir.Sentry.Node/v1")
$credentialEnvelope = [ordered]@{
    protectedAccessToken = Protect-ForCurrentUser ([string]$tokens.access_token) $entropy
    protectedRefreshToken = Protect-ForCurrentUser ([string]$tokens.refresh_token) $entropy
    protectedSigningKey = Protect-ForCurrentUser $signingKey $entropy
}
[IO.File]::WriteAllText(
    $credentialPath,
    ($credentialEnvelope | ConvertTo-Json),
    (New-Object Text.UTF8Encoding($false)))

$config = [ordered]@{
    gatewayUrl = $GatewayUrl
    nodeId = [string]$registration.node_id
    nodeName = $NodeName
    ownerUserId = $ownerUserId
    workspaces = @(
        [ordered]@{
            workspaceId = "server-work"
            rootPath = $serverWork
            allowedHarnesses = @("shell", "claude")
            allowedModes = @("readOnly", "workspaceWrite")
        },
        [ordered]@{
            workspaceId = "enfusion"
            rootPath = $enfusion
            allowedHarnesses = @("shell", "claude")
            allowedModes = @("readOnly", "workspaceWrite")
        }
    )
    teamMembers = @()
    credentialPath = $credentialPath
    logPath = $logPath
    claudeExecutable = $claudePath
    pollIntervalSeconds = 5
}
[IO.File]::WriteAllText(
    $configPath,
    ($config | ConvertTo-Json -Depth 8),
    (New-Object Text.UTF8Encoding($false)))

# One hidden, current-user task is the entire persistence layer. It starts at
# sign-in, never opens a console, and restarts after transient tunnel outages.
$existingTask = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if ($null -ne $existingTask) {
    Stop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
}
$action = New-ScheduledTaskAction -Execute $nodeExecutable -Argument ('"{0}"' -f $configPath)
$trigger = New-ScheduledTaskTrigger -AtLogOn -User "$env:USERDOMAIN\$env:USERNAME"
$principal = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" `
    -LogonType Interactive -RunLevel Limited
$settings = New-ScheduledTaskSettingsSet -Hidden -StartWhenAvailable `
    -MultipleInstances IgnoreNew -RestartCount 99 -RestartInterval (New-TimeSpan -Minutes 1) `
    -ExecutionTimeLimit ([TimeSpan]::Zero)
Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger `
    -Principal $principal -Settings $settings | Out-Null
Start-ScheduledTask -TaskName $TaskName

Start-Sleep -Seconds 4
$task = Get-ScheduledTask -TaskName $TaskName
if ($task.State -notin @('Running', 'Ready')) {
    throw "The Sentry workstation node task did not start correctly."
}

[pscustomobject]@{
    Installed = $true
    Node = $NodeName
    Workspaces = @('server-work', 'enfusion')
    TaskState = [string]$task.State
    Release = $revision
}

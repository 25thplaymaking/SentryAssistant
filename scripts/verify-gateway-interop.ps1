<#
.SYNOPSIS
    Proves the C# Sentry Node accepts a work order signed by the Python Gateway.

.DESCRIPTION
    Signs a work order with the Gateway's real sign_work_order, then validates it
    with the node's real WorkOrderValidator. Unit tests on either side cannot
    catch a wire-format disagreement between them; this can.

.EXAMPLE
    pwsh scripts/verify-gateway-interop.ps1
#>
[CmdletBinding()]
param(
    [string]$RepoRoot = (Split-Path -Parent $PSScriptRoot)
)

$ErrorActionPreference = 'Stop'

$gateway = Join-Path $RepoRoot 'server/sentry_gateway'
$python = Join-Path $gateway '.venv/Scripts/python.exe'
if (-not (Test-Path $python)) {
    throw "Gateway venv not found at $python. Create it and install pyjwt first."
}

# A throwaway key, used only for this round trip.
$key = 'interop-verification-key-padded-past-the-32-byte-minimum'

$signer = @'
import json, sys
from app.auth.work_order_signing import sign_work_order
from app.workorders.transitions import WorkOrderMode

signed = sign_work_order(
    signing_key=sys.argv[1],
    work_order_id="wo-interop",
    requesting_user_id="bryce",
    profile_id="p-interop",
    team_id=None,
    execution_node_id="node-interop",
    workspace_id="ws-sentry",
    harness="codex",
    mode=WorkOrderMode.READ_ONLY,
    correlation_id="corr-interop",
)
print(json.dumps({"token": signed.token, "nonce": signed.nonce}))
'@

Write-Host '=== signing a work order with the Python Gateway ===' -ForegroundColor Cyan
Push-Location $gateway
try {
    $signed = $signer | & $python - $key | ConvertFrom-Json
}
finally {
    Pop-Location
}

if (-not $signed.token) { throw 'Gateway did not produce a token.' }
Write-Host "  token issued (nonce $($signed.nonce.Substring(0,12))...)"

Write-Host ''
Write-Host '=== validating it with the C# Sentry Node ===' -ForegroundColor Cyan
$env:SENTRY_INTEROP_TOKEN = $signed.token
$env:SENTRY_INTEROP_KEY = $key
try {
    & 'C:\Program Files\dotnet\dotnet.exe' test `
        (Join-Path $RepoRoot 'tests/Sentry.Node.Tests/Sentry.Node.Tests.csproj') `
        -c Debug -p:Platform=x64 `
        --filter-class 'Sentry.Node.Tests.GatewayInteropTests'
    $exit = $LASTEXITCODE
}
finally {
    Remove-Item Env:SENTRY_INTEROP_TOKEN -ErrorAction SilentlyContinue
    Remove-Item Env:SENTRY_INTEROP_KEY -ErrorAction SilentlyContinue
}

if ($exit -ne 0) { throw "Interop verification FAILED (exit $exit)." }
Write-Host ''
Write-Host 'GATEWAY -> NODE INTEROP VERIFIED' -ForegroundColor Green

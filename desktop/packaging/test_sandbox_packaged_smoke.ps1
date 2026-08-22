param(
    [string]$BackendExe = ""
)

$ErrorActionPreference = "Stop"
$RepoRoot = Resolve-Path (Join-Path $PSScriptRoot "..\..")
if (-not $BackendExe) {
    $BackendExe = Join-Path $RepoRoot "desktop\packaging\dist\backend\medimage-backend.exe"
}
if (-not (Test-Path -LiteralPath $BackendExe -PathType Leaf)) {
    throw "Packaged backend executable not found: $BackendExe"
}
$BackendExe = (Resolve-Path -LiteralPath $BackendExe).Path

$Workspace = Join-Path ([System.IO.Path]::GetTempPath()) ("medimage-sandbox-smoke-" + [guid]::NewGuid().ToString("N"))
New-Item -ItemType Directory -Path $Workspace | Out-Null
try {
    Push-Location $Workspace
    foreach ($CaseId in @("write_allowed_output", "write_rawdata_denied", "write_outside_project_denied", "spawn_child_tree", "memory_limit", "timeout", "print_environment_keys")) {
        $Output = & $BackendExe --sandbox-self-test $CaseId 2>&1
        if ($LASTEXITCODE -ne 0) {
            throw "Sandbox self-test failed for ${CaseId} with exit code ${LASTEXITCODE}: $Output"
        }
        $Result = $Output | ConvertFrom-Json
        if (-not $Result.ok -or $Result.network_isolation -ne "not_enforced") {
            throw "Sandbox self-test emitted an invalid result for $CaseId"
        }
    }
} finally {
    Pop-Location
    if (Test-Path -LiteralPath $Workspace) {
        Remove-Item -LiteralPath $Workspace -Recurse -Force
    }
}

param(
    [string]$AppExe = "",
    [int]$TimeoutSeconds = 180
)

$ErrorActionPreference = "Stop"
$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
if (-not $AppExe) {
    $AppExe = Join-Path $RepoRoot "desktop\electron\dist\win-unpacked\MedImage Agent.exe"
}
if (-not (Test-Path -LiteralPath $AppExe -PathType Leaf)) {
    throw "Packaged Electron executable not found: $AppExe"
}
$AppExe = (Resolve-Path -LiteralPath $AppExe).Path
if ($TimeoutSeconds -le 0) {
    throw "TimeoutSeconds must be greater than zero."
}

$TempRoot = [System.IO.Path]::GetFullPath([System.IO.Path]::GetTempPath()).TrimEnd("\")
$SmokeRoot = Join-Path $TempRoot ("medimage-electron-smoke-" + [guid]::NewGuid().ToString("N"))
$UserData = Join-Path $SmokeRoot "user-data"
$Workspace = Join-Path $SmokeRoot "workspace"
$ResultPath = Join-Path $SmokeRoot "smoke-result.json"
$EnvironmentNames = @(
    "MEDIMAGE_DESKTOP_SMOKE",
    "MEDIMAGE_DESKTOP_SMOKE_RESULT",
    "MEDIMAGE_DESKTOP_USER_DATA",
    "MEDIMAGE_DESKTOP_WORKSPACE"
)
$PreviousEnvironment = @{}
$ElectronProcess = $null

New-Item -ItemType Directory -Path $UserData, $Workspace | Out-Null
try {
    foreach ($Name in $EnvironmentNames) {
        $PreviousEnvironment[$Name] = [Environment]::GetEnvironmentVariable($Name, "Process")
    }
    $env:MEDIMAGE_DESKTOP_SMOKE = "1"
    $env:MEDIMAGE_DESKTOP_SMOKE_RESULT = $ResultPath
    $env:MEDIMAGE_DESKTOP_USER_DATA = $UserData
    $env:MEDIMAGE_DESKTOP_WORKSPACE = $Workspace

    $ElectronProcess = Start-Process `
        -FilePath $AppExe `
        -WorkingDirectory (Split-Path -Parent $AppExe) `
        -WindowStyle Hidden `
        -PassThru
    if (-not $ElectronProcess.WaitForExit($TimeoutSeconds * 1000)) {
        $CurrentProcess = Get-Process -Id $ElectronProcess.Id -ErrorAction SilentlyContinue
        if ($CurrentProcess -and $CurrentProcess.Path -eq $AppExe) {
            & taskkill.exe /PID $ElectronProcess.Id /T /F | Out-Null
        }
        throw "Packaged Electron smoke timed out after $TimeoutSeconds seconds."
    }
    $ElectronProcess.Refresh()
    if ($ElectronProcess.ExitCode -ne 0) {
        throw "Packaged Electron smoke exited with code $($ElectronProcess.ExitCode)."
    }
    if (-not (Test-Path -LiteralPath $ResultPath -PathType Leaf)) {
        throw "Packaged Electron smoke did not write its result file."
    }

    $Result = Get-Content -LiteralPath $ResultPath -Raw | ConvertFrom-Json
    $Failures = @()
    if (-not $Result.frontendLoaded) { $Failures += "frontendLoaded" }
    if (-not $Result.rendererVerified) { $Failures += "rendererVerified" }
    if (-not $Result.backend.ready) { $Failures += "backend.ready" }
    if (-not $Result.renderer.backendConfigPresent) { $Failures += "renderer.backendConfigPresent" }
    if (-not $Result.renderer.rendererBackendHealthOk) { $Failures += "renderer.rendererBackendHealthOk" }
    if (-not $Result.renderer.mainLandmarkPresent) { $Failures += "renderer.mainLandmarkPresent" }
    if ($Result.renderer.reactRootChildCount -le 0) { $Failures += "renderer.reactRootChildCount" }
    if ($Result.renderer.reactRootTextLength -le 0) { $Failures += "renderer.reactRootTextLength" }
    if (@($Result.rendererConsoleErrors).Count -ne 0) { $Failures += "rendererConsoleErrors" }
    if ($Failures.Count -gt 0) {
        throw "Packaged Electron smoke evidence failed: $($Failures -join ', ')"
    }

    Start-Sleep -Milliseconds 500
    $BackendProcess = Get-Process -Id $Result.backend.pid -ErrorAction SilentlyContinue
    if ($BackendProcess -and $BackendProcess.Path -eq $Result.backend.executablePath) {
        throw "Managed backend sidecar remained alive after Electron exited: PID $($Result.backend.pid)"
    }

    [pscustomobject]@{
        ok = $true
        app = $AppExe
        backend_ready = $Result.backend.ready
        renderer_verified = $Result.rendererVerified
        renderer_backend_health_status = $Result.renderer.rendererBackendHealthStatus
        react_root_child_count = $Result.renderer.reactRootChildCount
        renderer_console_error_count = @($Result.rendererConsoleErrors).Count
        sidecar_stopped = $true
    } | ConvertTo-Json
}
finally {
    foreach ($Name in $EnvironmentNames) {
        $PreviousValue = $PreviousEnvironment[$Name]
        if ($null -eq $PreviousValue) {
            [Environment]::SetEnvironmentVariable($Name, $null, "Process")
        }
        else {
            [Environment]::SetEnvironmentVariable($Name, $PreviousValue, "Process")
        }
    }

    if (Test-Path -LiteralPath $SmokeRoot) {
        $ResolvedSmokeRoot = (Resolve-Path -LiteralPath $SmokeRoot).Path
        $ExpectedParent = Split-Path -Parent $ResolvedSmokeRoot
        $ExpectedLeaf = Split-Path -Leaf $ResolvedSmokeRoot
        if ($ExpectedParent -ne $TempRoot -or -not $ExpectedLeaf.StartsWith("medimage-electron-smoke-")) {
            throw "Refusing to clean unexpected smoke directory: $ResolvedSmokeRoot"
        }
        Remove-Item -LiteralPath $ResolvedSmokeRoot -Recurse -Force
    }
}

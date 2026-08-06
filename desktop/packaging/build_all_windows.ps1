param(
    [switch]$SkipFullPytest,
    [switch]$SkipDependencyInstall,
    [switch]$SkipNpmInstall,
    [string]$ElectronRuntimeZip,
    [string]$NsisArchive,
    [string]$NsisResourcesArchive,
    [switch]$DirOnly,
    [string]$PythonExe
)

$ErrorActionPreference = "Stop"
$RepoRoot = Resolve-Path (Join-Path $PSScriptRoot "..\..")

function Clear-PackagingResiduals {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Root
    )

    $resolvedRoot = (Resolve-Path -LiteralPath $Root).Path
    $failures = @()
    foreach ($pattern in @(".pytest_*", "_MEI*")) {
        $candidates = Get-ChildItem -LiteralPath $resolvedRoot -Force -Directory -Filter $pattern -ErrorAction SilentlyContinue
        foreach ($candidate in $candidates) {
            $resolvedCandidate = (Resolve-Path -LiteralPath $candidate.FullName).Path
            if (-not $resolvedCandidate.StartsWith($resolvedRoot, [System.StringComparison]::OrdinalIgnoreCase)) {
                throw "Refusing to clean residual outside repository root: $resolvedCandidate"
            }
            try {
                Remove-Item -LiteralPath $resolvedCandidate -Recurse -Force -ErrorAction Stop
                Write-Host "Removed packaging residual directory: $resolvedCandidate"
            }
            catch {
                $failures += "$resolvedCandidate :: $($_.Exception.Message)"
            }
        }
    }

    if ($failures.Count -gt 0) {
        $details = $failures -join [Environment]::NewLine
        $processHint = "No obvious Python/PyInstaller/pytest/MedImage processes were visible to this shell; check elevated or background processes if the path remains locked."
        try {
            $lockCandidates = @(Get-Process -ErrorAction SilentlyContinue |
                Where-Object { $_.ProcessName -match "python|pytest|pyinstaller|MedImage|medimage-backend|electron" } |
                Select-Object -ExpandProperty ProcessName -Unique)
            if ($lockCandidates.Count -gt 0) {
                $processHint = "Processes that may hold locks: $($lockCandidates -join ', ')"
            }
        }
        catch {
            $processHint = "Unable to inspect local process list; close Python, pytest, PyInstaller, Electron, and MedImage Agent processes before retrying."
        }
        throw "Unable to remove generated packaging/test residual directories:$([Environment]::NewLine)$details$([Environment]::NewLine)$processHint$([Environment]::NewLine)Close running pytest, Python, PyInstaller, or MedImage Agent processes and remove the listed paths before packaging again."
    }
}

function Invoke-PytestChecked {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Interpreter,
        [Parameter(Mandatory = $true)]
        [string[]]$Arguments,
        [Parameter(Mandatory = $true)]
        [string]$Root,
        [Parameter(Mandatory = $true)]
        [string]$Description
    )

    & $Interpreter -m pytest @Arguments
    $ExitCode = $LASTEXITCODE
    Clear-PackagingResiduals -Root $Root
    if ($ExitCode -ne 0) {
        throw "$Description failed with exit code $ExitCode."
    }
}

Push-Location $RepoRoot
try {
    Clear-PackagingResiduals -Root $RepoRoot
    $TestPython = if ($PythonExe) { $PythonExe } else { "python" }

    $FocusedTests = @(
        "tests/unit/test_desktop_backend_entry.py",
        "tests/unit/test_desktop_packaging_contract.py",
        "tests/unit/test_execute_reviewed_api.py",
        "-v",
        "--basetemp=.pytest_tmp_packaging_focused"
    )
    Invoke-PytestChecked `
        -Interpreter $TestPython `
        -Arguments $FocusedTests `
        -Root $RepoRoot `
        -Description "Focused packaging tests"

    if (-not $SkipFullPytest) {
        Invoke-PytestChecked `
            -Interpreter $TestPython `
            -Arguments @("--tb=short", "--basetemp=.pytest_tmp_packaging_full") `
            -Root $RepoRoot `
            -Description "Full backend test suite"
    }

    & (Join-Path $RepoRoot "desktop\packaging\build_frontend.ps1")
    $BackendArgs = @{ SkipDependencyInstall = $SkipDependencyInstall }
    if ($PythonExe) { $BackendArgs.PythonExe = $PythonExe }
    & (Join-Path $RepoRoot "desktop\packaging\build_backend.ps1") @BackendArgs

    $LauncherArgs = @{ SkipDependencyInstall = $SkipDependencyInstall }
    if ($PythonExe) { $LauncherArgs.PythonExe = $PythonExe }
    & (Join-Path $RepoRoot "desktop\packaging\build_launcher.ps1") @LauncherArgs
    $DesktopBuildArgs = @{
        SkipNpmInstall = $SkipNpmInstall
    }
    if ($ElectronRuntimeZip) {
        $DesktopBuildArgs.ElectronRuntimeZip = $ElectronRuntimeZip
    }
    if ($NsisArchive) {
        $DesktopBuildArgs.NsisArchive = $NsisArchive
    }
    if ($NsisResourcesArchive) {
        $DesktopBuildArgs.NsisResourcesArchive = $NsisResourcesArchive
    }
    if ($DirOnly) {
        $DesktopBuildArgs.DirOnly = $true
    }
    & (Join-Path $RepoRoot "desktop\packaging\build_desktop.ps1") @DesktopBuildArgs

    Write-Host "Windows desktop packaging complete."
    Write-Host "Electron installer/portable artifacts are under desktop\electron\dist."
    Write-Host "PyInstaller launcher fallback is under desktop\packaging\dist\launcher."
}
finally {
    Pop-Location
}

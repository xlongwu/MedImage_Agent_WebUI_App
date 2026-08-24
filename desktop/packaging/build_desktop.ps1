param(
    [switch]$SkipNpmInstall,
    [string]$ElectronRuntimeZip,
    [string]$NsisArchive,
    [string]$NsisResourcesArchive,
    [switch]$DirOnly,
    [string]$PythonExe,
    [string]$ExpectedGitSha,
    [switch]$RequireCleanWorktree
)

$ErrorActionPreference = "Stop"
$RepoRoot = Resolve-Path (Join-Path $PSScriptRoot "..\..")
$ElectronRoot = Join-Path $RepoRoot "desktop\electron"
$ElectronDist = Join-Path $ElectronRoot "dist"
$FrontendBuildScript = Join-Path $RepoRoot "desktop\packaging\build_frontend.ps1"
$BackendDist = Join-Path $RepoRoot "desktop\packaging\dist\backend"
$BackendExe = Join-Path $BackendDist "medimage-backend.exe"
$BackendPayloadDir = Join-Path $RepoRoot "desktop\packaging\dist\backend_payload"
$BackendPayload = Join-Path $BackendPayloadDir "medimage-backend.bin"
$ReleaseMetadataDir = Join-Path $RepoRoot "desktop\packaging\dist\release_metadata"
$BuildProvenance = Join-Path $ReleaseMetadataDir "build-provenance.json"
$ReleaseArtifacts = Join-Path $ReleaseMetadataDir "release-artifacts.json"
$ProvenanceWriter = Join-Path $RepoRoot "desktop\packaging\write_release_provenance.py"
$ProvenancePython = if ($PythonExe) { $PythonExe } else { "python" }

function Invoke-NpmChecked {
    param(
        [Parameter(Mandatory = $true)]
        [string[]]$Arguments,
        [Parameter(Mandatory = $true)]
        [string]$Description
    )

    & npm @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "$Description failed with exit code $LASTEXITCODE"
    }
}

Push-Location $RepoRoot
try {
    # Always build the renderer through the packaging entry point. A plain
    # frontend build leaves the reviewed DICOM execution UI disabled.
    & powershell.exe -ExecutionPolicy Bypass -File $FrontendBuildScript
    if ($LASTEXITCODE -ne 0) {
        throw "frontend static build failed with exit code $LASTEXITCODE"
    }

    if (-not $SkipNpmInstall) {
        Push-Location $ElectronRoot
        try {
            Invoke-NpmChecked -Arguments @("install") -Description "npm install"
        }
        finally {
            Pop-Location
        }
    }

    if (-not (Test-Path -LiteralPath $BackendExe)) {
        throw "Backend sidecar not found: $BackendExe. Run desktop/packaging/build_backend.ps1 first."
    }
    if (Test-Path -LiteralPath $BackendPayloadDir) {
        Remove-Item -LiteralPath $BackendPayloadDir -Recurse -Force
    }
    New-Item -ItemType Directory -Force -Path $BackendPayloadDir | Out-Null
    Copy-Item -LiteralPath $BackendExe -Destination $BackendPayload -Force

    $EffectiveRequireClean = $RequireCleanWorktree -or [bool]$ExpectedGitSha
    $ProvenanceArgs = @(
        $ProvenanceWriter,
        "--repo-root", $RepoRoot.Path,
        "--output", $BuildProvenance
    )
    if ($ExpectedGitSha) {
        $ProvenanceArgs += @("--expected-sha", $ExpectedGitSha)
    }
    if ($EffectiveRequireClean) {
        $ProvenanceArgs += "--require-clean"
    }
    & $ProvenancePython @ProvenanceArgs
    if ($LASTEXITCODE -ne 0) {
        throw "Release build provenance generation failed with exit code $LASTEXITCODE"
    }

    if (Test-Path -LiteralPath $ElectronDist) {
        $ResolvedElectronRoot = Resolve-Path -LiteralPath $ElectronRoot
        $ResolvedElectronDist = Resolve-Path -LiteralPath $ElectronDist
        $ExpectedPrefix = $ResolvedElectronRoot.Path + [System.IO.Path]::DirectorySeparatorChar
        if (-not $ResolvedElectronDist.Path.StartsWith($ExpectedPrefix) -or (Split-Path -Leaf $ResolvedElectronDist.Path) -ne "dist") {
            throw "Refusing to remove unexpected Electron dist path: $($ResolvedElectronDist.Path)"
        }
        Remove-Item -LiteralPath $ResolvedElectronDist.Path -Recurse -Force
    }

    Invoke-NpmChecked -Arguments @("--prefix", "desktop/electron", "run", "check") -Description "desktop Electron check"

    $PreviousRuntimeZip = $env:MEDIMAGE_ELECTRON_RUNTIME_ZIP
    $PreviousNsisArchive = $env:MEDIMAGE_ELECTRON_NSIS_ARCHIVE
    $PreviousNsisResourcesArchive = $env:MEDIMAGE_ELECTRON_NSIS_RESOURCES_ARCHIVE
    if ($ElectronRuntimeZip) {
        $ResolvedRuntimeZip = Resolve-Path -LiteralPath $ElectronRuntimeZip
        $env:MEDIMAGE_ELECTRON_RUNTIME_ZIP = $ResolvedRuntimeZip.Path
    }
    if ($NsisArchive) {
        $ResolvedNsisArchive = Resolve-Path -LiteralPath $NsisArchive
        $env:MEDIMAGE_ELECTRON_NSIS_ARCHIVE = $ResolvedNsisArchive.Path
    }
    if ($NsisResourcesArchive) {
        $ResolvedNsisResourcesArchive = Resolve-Path -LiteralPath $NsisResourcesArchive
        $env:MEDIMAGE_ELECTRON_NSIS_RESOURCES_ARCHIVE = $ResolvedNsisResourcesArchive.Path
    }
    try {
        if ($DirOnly) {
            Invoke-NpmChecked -Arguments @("--prefix", "desktop/electron", "run", "dist:dir") -Description "desktop Electron dir dist"
        }
        else {
            Invoke-NpmChecked -Arguments @("--prefix", "desktop/electron", "run", "dist") -Description "desktop Electron dist"
        }
    }
    finally {
        $env:MEDIMAGE_ELECTRON_RUNTIME_ZIP = $PreviousRuntimeZip
        $env:MEDIMAGE_ELECTRON_NSIS_ARCHIVE = $PreviousNsisArchive
        $env:MEDIMAGE_ELECTRON_NSIS_RESOURCES_ARCHIVE = $PreviousNsisResourcesArchive
    }

    & $ProvenancePython $ProvenanceWriter `
        --repo-root $RepoRoot.Path `
        --output $BuildProvenance `
        --artifact-root $ElectronDist `
        --artifact-output $ReleaseArtifacts
    if ($LASTEXITCODE -ne 0) {
        throw "Release artifact manifest generation failed with exit code $LASTEXITCODE"
    }

    $DistPath = Join-Path $ElectronRoot "dist"
    Write-Host "Desktop artifacts:"
    Get-ChildItem $DistPath -File -Recurse |
        Where-Object {
            $_.Name -like "*.exe" -or
            $_.Name -eq "builder-debug.yml" -or
            $_.Name -eq "latest.yml"
        } |
        Select-Object FullName, Length, LastWriteTime |
        Format-Table -AutoSize
}
finally {
    Pop-Location
}

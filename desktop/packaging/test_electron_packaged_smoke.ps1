param(
    [string]$AppExe = "",
    [int]$TimeoutSeconds = 180,
    [switch]$Visible,
    [ValidateSet("shell", "bids", "dicom", "recovery")]
    [string]$Workflow = "shell",
    [string]$ExpectedGitSha = "",
    [string]$EvidenceDir = ""
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
if ($ExpectedGitSha -and -not $EvidenceDir) {
    throw "EvidenceDir is required when ExpectedGitSha is provided."
}
$EvidenceOutput = $null
if ($EvidenceDir) {
    $EvidenceCandidate = if ([System.IO.Path]::IsPathRooted($EvidenceDir)) {
        [System.IO.Path]::GetFullPath($EvidenceDir)
    }
    else {
        [System.IO.Path]::GetFullPath((Join-Path (Get-Location) $EvidenceDir))
    }
    if (Test-Path -LiteralPath $EvidenceCandidate) {
        if (-not (Test-Path -LiteralPath $EvidenceCandidate -PathType Container)) {
            throw "EvidenceDir must be a directory: $EvidenceCandidate"
        }
        if (Get-ChildItem -LiteralPath $EvidenceCandidate -Force | Select-Object -First 1) {
            throw "Refusing to overwrite non-empty evidence directory: $EvidenceCandidate"
        }
    }
    else {
        New-Item -ItemType Directory -Path $EvidenceCandidate | Out-Null
    }
    $EvidenceOutput = (Resolve-Path -LiteralPath $EvidenceCandidate).Path
}

$SmokeParent = Join-Path ([System.IO.Path]::GetTempPath()) "mia"
New-Item -ItemType Directory -Force -Path $SmokeParent | Out-Null
$SmokeParent = (Resolve-Path -LiteralPath $SmokeParent).Path
$SmokeRoot = Join-Path $SmokeParent ("e-" + [guid]::NewGuid().ToString("N").Substring(0, 6))
$UserData = Join-Path $SmokeRoot "u"
$Workspace = Join-Path $SmokeRoot "w"
$Rawdata = if ($Workflow -eq "bids") {
    (Resolve-Path -LiteralPath (Join-Path $RepoRoot "examples\synthetic_bids\rawdata")).Path
}
elseif ($Workflow -in @("dicom", "recovery")) {
    Join-Path $SmokeRoot "rawdata"
}
else {
    Join-Path $SmokeRoot "rawdata"
}
$SubjectFunc = Join-Path $Rawdata "sub-01\func"
$ProjectDir = Join-Path $Workspace "p"
$ResultPath = Join-Path $SmokeRoot "smoke-result.json"
$ElectronStdoutPath = Join-Path $SmokeRoot "electron.stdout.log"
$ElectronStderrPath = Join-Path $SmokeRoot "electron.stderr.log"
$ScreenshotPath = Join-Path $SmokeRoot "final-screenshot.png"
$AtlasSource = Join-Path $SmokeRoot "agent-first-smoke-atlas.nii.gz"
$TemplateSource = Join-Path $SmokeRoot "agent-first-smoke-template.nii.gz"
$EnvironmentNames = @(
    "MEDIMAGE_DESKTOP_SMOKE",
    "MEDIMAGE_DESKTOP_SMOKE_RESULT",
    "MEDIMAGE_DESKTOP_VISIBLE_SMOKE",
    "MEDIMAGE_DESKTOP_SMOKE_RAWDATA",
    "MEDIMAGE_DESKTOP_SMOKE_PROJECT_DIR",
    "MEDIMAGE_DESKTOP_SMOKE_WORKFLOW",
    "MEDIMAGE_DESKTOP_SMOKE_ATLAS_SOURCE",
    "MEDIMAGE_DESKTOP_SMOKE_TEMPLATE_SOURCE",
    "MEDIMAGE_DESKTOP_SMOKE_SCREENSHOT",
    "MEDIMAGE_ENABLE_REVIEWED_EXECUTION",
    "MEDIMAGE_ALLOW_SANDBOXED_FC",
    "MEDIMAGE_ENABLE_DICOM_CONVERSION",
    "MEDIMAGE_ALLOW_USER_DATA_CONVERSION",
    "MEDIMAGE_DESKTOP_USER_DATA",
    "MEDIMAGE_DESKTOP_WORKSPACE"
)
$PreviousEnvironment = @{}
$ElectronProcess = $null
$Result = $null
$SmokeFailure = $null

function Save-GateEvidence {
    if (-not $EvidenceOutput) { return }

    $Replacements = @(
        @($SmokeRoot, "<SMOKE_ROOT>"),
        @($RepoRoot, "<REPO_ROOT>"),
        @($AppExe, "<PACKAGED_APP>")
    )
    $TextSources = @(
        @($ResultPath, "smoke-result.json"),
        @($ElectronStdoutPath, "electron.stdout.log"),
        @($ElectronStderrPath, "electron.stderr.log"),
        @((Join-Path $Workspace "logs\desktop\backend-sidecar.log"), "backend-sidecar.log")
    )
    foreach ($Source in $TextSources) {
        if (-not (Test-Path -LiteralPath $Source[0] -PathType Leaf)) { continue }
        $Content = [System.IO.File]::ReadAllText($Source[0])
        foreach ($Replacement in $Replacements) {
            if ($Replacement[0]) { $Content = $Content.Replace($Replacement[0], $Replacement[1]) }
        }
        [System.IO.File]::WriteAllText(
            (Join-Path $EvidenceOutput $Source[1]),
            $Content,
            [System.Text.UTF8Encoding]::new($false)
        )
    }
    if (Test-Path -LiteralPath $ScreenshotPath -PathType Leaf) {
        Copy-Item -LiteralPath $ScreenshotPath -Destination (Join-Path $EvidenceOutput "final-screenshot.png")
    }
    $PackagedProvenance = Join-Path (Split-Path -Parent $AppExe) "resources\release\build-provenance.json"
    if (Test-Path -LiteralPath $PackagedProvenance -PathType Leaf) {
        Copy-Item -LiteralPath $PackagedProvenance -Destination (Join-Path $EvidenceOutput "build-provenance.json")
    }
    $ReleaseArtifacts = Join-Path $RepoRoot "desktop\packaging\dist\release_metadata\release-artifacts.json"
    if (Test-Path -LiteralPath $ReleaseArtifacts -PathType Leaf) {
        Copy-Item -LiteralPath $ReleaseArtifacts -Destination (Join-Path $EvidenceOutput "release-artifacts.json")
    }
    [System.IO.File]::WriteAllText(
        (Join-Path $EvidenceOutput "rawdata-before.json"),
        $RawdataManifestBefore,
        [System.Text.UTF8Encoding]::new($false)
    )
    $CurrentRawdataManifest = if ($Workflow -in @("bids", "dicom", "recovery") -and (Test-Path -LiteralPath $Rawdata)) {
        @(
            Get-ChildItem -LiteralPath $Rawdata -Recurse -File |
                Sort-Object FullName |
                ForEach-Object {
                    [pscustomobject]@{
                        path = $_.FullName.Substring($Rawdata.Length).TrimStart("\")
                        size = $_.Length
                        sha256 = (Get-FileHash -LiteralPath $_.FullName -Algorithm SHA256).Hash
                    }
                }
        ) | ConvertTo-Json -Compress
    }
    else { "[]" }
    [System.IO.File]::WriteAllText(
        (Join-Path $EvidenceOutput "rawdata-after.json"),
        $CurrentRawdataManifest,
        [System.Text.UTF8Encoding]::new($false)
    )
    $GateSummary = [ordered]@{
        _schema_version = 1
        ok = (-not $SmokeFailure)
        expected_git_sha = $ExpectedGitSha
        actual_git_sha = if ($Result) { $Result.buildProvenance.git.sha } else { $null }
        clean_source = if ($Result) { $Result.buildProvenance.git.clean } else { $null }
        visible = [bool]$Visible
        workflow = $Workflow
        rawdata_unchanged = ($RawdataManifestBefore -eq $CurrentRawdataManifest)
        renderer_console_error_count = if ($Result) { @($Result.rendererConsoleErrors).Count } else { $null }
        explicit_operations = if (-not $Result) { $null } elseif ($Workflow -in @("bids", "dicom")) { $Result.bidsToFc.explicitOperations } elseif ($Workflow -eq "recovery") { $Result.recovery.explicitOperations } else { 0 }
        outcome = if (-not $Result) { $null } elseif ($Workflow -in @("bids", "dicom")) { $Result.bidsToFc.task.outcome } elseif ($Workflow -eq "recovery") { $Result.recovery.task.outcome } else { "shell_verified" }
        failure = $SmokeFailure
    }
    $GateSummary | ConvertTo-Json -Depth 10 | Set-Content -LiteralPath (Join-Path $EvidenceOutput "gate-summary.json") -Encoding utf8
}

New-Item -ItemType Directory -Path $UserData, $Workspace | Out-Null
if ($Workflow -eq "shell") {
    New-Item -ItemType Directory -Path $SubjectFunc | Out-Null
}
else {
    if ($Workflow -eq "dicom") {
        $DicomSource = (Resolve-Path -LiteralPath (Join-Path $RepoRoot "data\DemoData\FunRaw")).Path
        $SmokeFunRaw = Join-Path $Rawdata "FunRaw"
        New-Item -ItemType Directory -Path $SmokeFunRaw | Out-Null
        foreach ($SourceSubject in Get-ChildItem -LiteralPath $DicomSource -Directory | Sort-Object Name) {
            $TargetSubject = Join-Path $SmokeFunRaw $SourceSubject.Name
            New-Item -ItemType Directory -Path $TargetSubject | Out-Null
            Get-ChildItem -LiteralPath $SourceSubject.FullName -File |
                Sort-Object Name |
                Select-Object -First 12 |
                Copy-Item -Destination $TargetSubject
        }
    }
    elseif ($Workflow -eq "recovery") {
        $RecoverySource = (Resolve-Path -LiteralPath (Join-Path $RepoRoot "examples\synthetic_bids\rawdata")).Path
        New-Item -ItemType Directory -Path $Rawdata | Out-Null
        Copy-Item -Path (Join-Path $RecoverySource "*") -Destination $Rawdata -Recurse -Force
        $RecoverySubjectSource = Join-Path $Rawdata "sub-001"
        $RecoverySubjectTarget = Join-Path $Rawdata "sub-003"
        if (-not (Test-Path -LiteralPath $RecoverySubjectSource -PathType Container)) {
            throw "Recovery smoke source subject was not found."
        }
        Copy-Item -LiteralPath $RecoverySubjectSource -Destination $RecoverySubjectTarget -Recurse -Force
    }
    $FixturePython = Join-Path $RepoRoot ".venv\Scripts\python.exe"
    $FixtureScript = Join-Path $RepoRoot "desktop\packaging\create_agent_first_e2e_fixture.py"
    if ($Workflow -in @("bids", "recovery")) {
        $FixtureBold = Join-Path $Rawdata "sub-001\func\sub-001_task-rest_bold.nii.gz"
        & $FixturePython $FixtureScript --bold $FixtureBold --atlas $AtlasSource --template $TemplateSource
    }
    else {
        $FixtureDicom = Join-Path $Rawdata "FunRaw\Sub_001"
        & $FixturePython $FixtureScript --dicom-dir $FixtureDicom --atlas $AtlasSource --template $TemplateSource
    }
    if (
        $LASTEXITCODE -ne 0 -or
        -not (Test-Path -LiteralPath $AtlasSource -PathType Leaf) -or
        -not (Test-Path -LiteralPath $TemplateSource -PathType Leaf)
    ) {
        throw "Failed to create the isolated $Workflow E2E scientific resource fixtures."
    }
}
$RawdataManifestBefore = if ($Workflow -in @("bids", "dicom", "recovery")) {
    @(
        Get-ChildItem -LiteralPath $Rawdata -Recurse -File |
            Sort-Object FullName |
            ForEach-Object {
                [pscustomobject]@{
                    path = $_.FullName.Substring($Rawdata.Length).TrimStart("\")
                    size = $_.Length
                    sha256 = (Get-FileHash -LiteralPath $_.FullName -Algorithm SHA256).Hash
                }
            }
    ) | ConvertTo-Json -Compress
}
else { "[]" }
try {
    foreach ($Name in $EnvironmentNames) {
        $PreviousEnvironment[$Name] = [Environment]::GetEnvironmentVariable($Name, "Process")
    }
    $env:MEDIMAGE_DESKTOP_SMOKE = "1"
    $env:MEDIMAGE_DESKTOP_SMOKE_RESULT = $ResultPath
    $env:MEDIMAGE_DESKTOP_VISIBLE_SMOKE = if ($Visible) { "1" } else { "0" }
    $env:MEDIMAGE_DESKTOP_SMOKE_RAWDATA = $Rawdata
    $env:MEDIMAGE_DESKTOP_SMOKE_PROJECT_DIR = $ProjectDir
    $env:MEDIMAGE_DESKTOP_SMOKE_WORKFLOW = $Workflow
    $env:MEDIMAGE_DESKTOP_SMOKE_ATLAS_SOURCE = if ($Workflow -in @("bids", "dicom", "recovery")) { $AtlasSource } else { "" }
    $env:MEDIMAGE_DESKTOP_SMOKE_TEMPLATE_SOURCE = if ($Workflow -in @("bids", "dicom", "recovery")) { $TemplateSource } else { "" }
    $env:MEDIMAGE_DESKTOP_SMOKE_SCREENSHOT = if ($Visible) { $ScreenshotPath } else { "" }
    $env:MEDIMAGE_DESKTOP_USER_DATA = $UserData
    $env:MEDIMAGE_DESKTOP_WORKSPACE = $Workspace
    $env:MEDIMAGE_ENABLE_REVIEWED_EXECUTION = if ($Workflow -in @("bids", "dicom", "recovery")) { "1" } else { "" }
    $env:MEDIMAGE_ALLOW_SANDBOXED_FC = if ($Workflow -in @("bids", "dicom", "recovery")) { "1" } else { "" }
    $env:MEDIMAGE_ENABLE_DICOM_CONVERSION = if ($Workflow -eq "dicom") { "1" } else { "" }
    $env:MEDIMAGE_ALLOW_USER_DATA_CONVERSION = if ($Workflow -eq "dicom") { "1" } else { "" }

    $StartArguments = @{
        FilePath = $AppExe
        WorkingDirectory = (Split-Path -Parent $AppExe)
        PassThru = $true
        RedirectStandardOutput = $ElectronStdoutPath
        RedirectStandardError = $ElectronStderrPath
    }
    if (-not $Visible) {
        $StartArguments.WindowStyle = "Hidden"
    }
    $ElectronProcess = Start-Process @StartArguments
    if (-not $ElectronProcess.WaitForExit($TimeoutSeconds * 1000)) {
        $CurrentProcess = Get-Process -Id $ElectronProcess.Id -ErrorAction SilentlyContinue
        if ($CurrentProcess -and $CurrentProcess.Path -eq $AppExe) {
            & taskkill.exe /PID $ElectronProcess.Id /T /F | Out-Null
        }
        throw "Packaged Electron smoke timed out after $TimeoutSeconds seconds."
    }
    $ElectronProcess.Refresh()
    $ElectronExitCode = $ElectronProcess.ExitCode
    if ($null -ne $ElectronExitCode -and $ElectronExitCode -ne 0) {
        $Evidence = if (Test-Path -LiteralPath $ResultPath -PathType Leaf) {
            Get-Content -LiteralPath $ResultPath -Raw
        }
        else { "result file missing" }
        $BackendLogPath = Join-Path $Workspace "logs\desktop\backend-sidecar.log"
        $BackendLog = if (Test-Path -LiteralPath $BackendLogPath -PathType Leaf) {
            Get-Content -LiteralPath $BackendLogPath -Raw
        }
        else { "backend log missing" }
        $ElectronStderr = if (Test-Path -LiteralPath $ElectronStderrPath -PathType Leaf) {
            Get-Content -LiteralPath $ElectronStderrPath -Raw
        }
        else { "electron stderr missing" }
        throw "Packaged Electron smoke exited with code $ElectronExitCode; evidence=$Evidence; electron_stderr=$ElectronStderr; backend_log=$BackendLog"
    }
    if (-not (Test-Path -LiteralPath $ResultPath -PathType Leaf)) {
        throw "Packaged Electron smoke did not write its result file."
    }

    $Result = Get-Content -LiteralPath $ResultPath -Raw | ConvertFrom-Json
    $Failures = @()
    if (-not $Result.frontendLoaded) { $Failures += "frontendLoaded" }
    if (-not $Result.rendererVerified) { $Failures += "rendererVerified" }
    if (-not $Result.backend.ready) { $Failures += "backend.ready" }
    if (-not $Result.backend.managed) { $Failures += "backend.managed" }
    if (-not $Result.renderer.backendConfigPresent) { $Failures += "renderer.backendConfigPresent" }
    if (-not $Result.renderer.rendererBackendHealthOk) { $Failures += "renderer.rendererBackendHealthOk" }
    if (-not $Result.renderer.mainLandmarkPresent) { $Failures += "renderer.mainLandmarkPresent" }
    if ($Result.renderer.reactRootChildCount -le 0) { $Failures += "renderer.reactRootChildCount" }
    if ($Result.renderer.reactRootTextLength -le 0) { $Failures += "renderer.reactRootTextLength" }
    if (@($Result.rendererConsoleErrors).Count -ne 0) { $Failures += "rendererConsoleErrors" }
    if (-not $Result.buildProvenance.valid) { $Failures += "buildProvenance.valid" }
    if ($ExpectedGitSha -and $Result.buildProvenance.git.sha -ne $ExpectedGitSha.ToLowerInvariant()) { $Failures += "buildProvenance.git.sha" }
    if ($ExpectedGitSha -and -not $Result.buildProvenance.git.clean) { $Failures += "buildProvenance.git.clean" }
    if ($Visible -and -not $Result.finalScreenshot) { $Failures += "finalScreenshot" }
    if ($Result.agentFirstNavigation.navigationCount -ne 4) { $Failures += "agentFirstNavigation.navigationCount" }
    if ($Result.agentFirstNavigation.disabledCount -ne 0) { $Failures += "agentFirstNavigation.disabledCount" }
    if (@($Result.agentFirstNavigation.visited).Count -ne 4) { $Failures += "agentFirstNavigation.visited" }
    foreach ($Visit in @($Result.agentFirstNavigation.visited)) {
        if ($Visit.index -ne $Visit.selectedIndex) { $Failures += "agentFirstNavigation.selectedIndex" }
    }
    if ($Workflow -in @("bids", "dicom")) {
        $MaximumOperations = if ($Workflow -eq "dicom") { 4 } else { 3 }
        if ($Result.bidsToFc.explicitOperations -gt $MaximumOperations) { $Failures += "bidsToFc.explicitOperations" }
        if ($Result.bidsToFc.decisionSubmissions -lt 1) { $Failures += "bidsToFc.decisionSubmissions" }
        if ($Workflow -eq "dicom" -and $Result.bidsToFc.decisionSubmissions -gt 2) { $Failures += "bidsToFc.decisionSubmissions" }
        if (-not $Result.bidsToFc.approvalSubmitted) { $Failures += "bidsToFc.approvalSubmitted" }
        if (-not $Result.bidsToFc.resultVisible) { $Failures += "bidsToFc.resultVisible" }
        $TruthfulPartial = (
            $Result.bidsToFc.task.state -eq "needs_attention" -and
            $Result.bidsToFc.task.outcome -eq "partial" -and
            $Result.bidsToFc.truthfulPartialHandoff
        )
        if ($Result.bidsToFc.task.state -ne "completed" -and -not $TruthfulPartial) {
            $Failures += "bidsToFc.task.state"
        }
        if ($Result.bidsToFc.task.outcome -notin @("satisfied", "partial")) { $Failures += "bidsToFc.task.outcome" }
        $FcArtifacts = @($Result.bidsToFc.task.result_summary.artifacts | Where-Object {
            $_.artifact_type -eq "fc_matrix" -and $_.reload_status -eq "passed"
        })
        if ($FcArtifacts.Count -eq 0) { $Failures += "bidsToFc.fcArtifact" }
        if (-not $Result.bidsToFc.task.result_summary.report_export_uri) { $Failures += "bidsToFc.reportExportUri" }
        if (-not $Result.bidsToFc.task.technical_details.run_id) { $Failures += "bidsToFc.runId" }
        if (-not $Result.bidsToFc.task.technical_details.evaluation_id) { $Failures += "bidsToFc.evaluationId" }
        if (-not $Result.bidsToFc.runEvidence.resourceProvenance) { $Failures += "bidsToFc.resourceProvenance" }
        if ($Workflow -eq "dicom") {
            $SuccessfulNodes = @($Result.bidsToFc.runEvidence.nodeStates | Where-Object {
                $_.state.status -in @("succeeded", "SUCCESS", "completed")
            } | ForEach-Object { $_.state.node })
            if ($SuccessfulNodes -notcontains "native_dicom_conversion_execute") { $Failures += "dicom.conversionNode" }
            if ($SuccessfulNodes -notcontains "native_preproc_full_execute") { $Failures += "dicom.preprocessingNode" }
            $FcStages = @($Result.bidsToFc.runEvidence.nativeManifest.subject_execution | ForEach-Object {
                $Manifest = Get-Content -LiteralPath $_.manifest_path -Raw | ConvertFrom-Json
                @($Manifest.stage_results | Where-Object {
                    $_.stage_id -eq "functional_connectivity" -and
                    $_.status -in @("succeeded", "warning") -and
                    @($_.output_artifacts).Count -gt 0
                })
            })
            if ($FcStages.Count -eq 0) { $Failures += "dicom.functionalConnectivityStage" }
        }
    }
    if ($Workflow -eq "recovery") {
        if (-not $Result.recovery.initialApprovalSubmitted) { $Failures += "recovery.initialApprovalSubmitted" }
        if (-not $Result.recovery.recoveryApprovalSubmitted) { $Failures += "recovery.recoveryApprovalSubmitted" }
        if (-not $Result.recovery.recoveryInputRestored) { $Failures += "recovery.recoveryInputRestored" }
        if ($Result.recovery.explicitOperations -ne 1) { $Failures += "recovery.explicitOperations" }
        if (@($Result.recovery.proposalTask.recovery.affected_subjects).Count -ne 1) { $Failures += "recovery.affectedSubjects" }
        $RecoverySatisfied = (
            $Result.recovery.task.state -eq "completed" -and
            $Result.recovery.task.outcome -eq "satisfied"
        )
        $RecoveryTruthfulPartial = (
            $Result.recovery.task.state -eq "needs_attention" -and
            $Result.recovery.task.outcome -eq "partial" -and
            $Result.recovery.truthfulPartialHandoff
        )
        if (-not ($RecoverySatisfied -or $RecoveryTruthfulPartial)) { $Failures += "recovery.task.terminalOutcome" }
        if (-not $Result.recovery.task.technical_details.evaluation_id) { $Failures += "recovery.evaluation" }
        if ($Result.recovery.task.technical_details.evaluation_id -eq $Result.recovery.proposalTask.technical_details.evaluation_id) { $Failures += "recovery.evaluationUnchanged" }
        if ($Result.recovery.latestRecoveryAttempt.status -ne "EVALUATED") { $Failures += "recovery.attemptStatus" }
        if ($Result.recovery.latestRecoveryAttempt.execution_status -notin @("SUCCESS", "COMPLETED")) { $Failures += "recovery.attemptExecutionStatus" }
        if (@($Result.recovery.latestRecoveryAttempt.target_subject_ids).Count -ne 1 -or $Result.recovery.latestRecoveryAttempt.target_subject_ids[0] -ne "sub-003") { $Failures += "recovery.attemptTargetSubjects" }
        if ($Result.recovery.latestRecoveryAttempt.goal_evaluation_id -ne $Result.recovery.task.technical_details.evaluation_id) { $Failures += "recovery.attemptEvaluationBinding" }
        if (@($Result.recovery.untouchedArtifactsBefore).Count -eq 0) { $Failures += "recovery.untouchedArtifacts" }
        if (($Result.recovery.untouchedArtifactsBefore | ConvertTo-Json -Depth 10 -Compress) -ne ($Result.recovery.untouchedArtifactsAfter | ConvertTo-Json -Depth 10 -Compress)) { $Failures += "recovery.untouchedArtifactsChanged" }
    }
    if ($Failures.Count -gt 0) {
        throw "Packaged Electron smoke evidence failed: $($Failures -join ', '). Full evidence will be saved to '$EvidenceOutput'."
    }

    $RawdataManifestAfter = if ($Workflow -in @("bids", "dicom", "recovery")) {
        @(
            Get-ChildItem -LiteralPath $Rawdata -Recurse -File |
                Sort-Object FullName |
                ForEach-Object {
                    [pscustomobject]@{
                        path = $_.FullName.Substring($Rawdata.Length).TrimStart("\")
                        size = $_.Length
                        sha256 = (Get-FileHash -LiteralPath $_.FullName -Algorithm SHA256).Hash
                    }
                }
        ) | ConvertTo-Json -Compress
    }
    else { "[]" }
    if ($RawdataManifestBefore -ne $RawdataManifestAfter) {
        throw "The $Workflow rawdata manifest changed during the packaged workflow."
    }

    Start-Sleep -Milliseconds 750
    $BackendProcesses = @(
        Get-Process -Name "medimage-backend" -ErrorAction SilentlyContinue |
            Where-Object { $_.Path -eq $Result.backend.executablePath }
    )
    if ($BackendProcesses.Count -ne 0) {
        throw "Managed packaged backend remained alive after Electron exited: $($BackendProcesses.Id -join ',')"
    }

    [pscustomobject]@{
        ok = $true
        app = $AppExe
        backend_ready = $Result.backend.ready
        backend_managed = $Result.backend.managed
        renderer_verified = $Result.rendererVerified
        renderer_backend_health_status = $Result.renderer.rendererBackendHealthStatus
        react_root_child_count = $Result.renderer.reactRootChildCount
        renderer_console_error_count = @($Result.rendererConsoleErrors).Count
        agent_first_navigation_count = $Result.agentFirstNavigation.navigationCount
        agent_first_routes_visited = @($Result.agentFirstNavigation.visited).Count
        visible = [bool]$Visible
        workflow = $Workflow
        explicit_operations = if ($Workflow -in @("bids", "dicom")) { $Result.bidsToFc.explicitOperations } elseif ($Workflow -eq "recovery") { $Result.recovery.explicitOperations } else { 0 }
        workflow_outcome = if ($Workflow -in @("bids", "dicom")) { $Result.bidsToFc.task.outcome } elseif ($Workflow -eq "recovery") { $Result.recovery.task.outcome } else { "shell_verified" }
        rawdata_unchanged = ($RawdataManifestBefore -eq $RawdataManifestAfter)
        sidecar_stopped = $true
    } | ConvertTo-Json
}
catch {
    $SmokeFailure = $_.Exception.Message
    throw
}
finally {
    Save-GateEvidence
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
        if ($ExpectedParent -ne $SmokeParent -or -not $ExpectedLeaf.StartsWith("e-")) {
            throw "Refusing to clean unexpected smoke directory: $ResolvedSmokeRoot"
        }
        Remove-Item -LiteralPath $ResolvedSmokeRoot -Recurse -Force
    }
}

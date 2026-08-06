from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def read(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


def test_desktop_packaging_files_exist():
    required = [
        "desktop/electron/package.json",
        "desktop/electron/main.cjs",
        "desktop/electron/preload.cjs",
        "desktop/electron/build-dist.cjs",
        "desktop/electron/electron-builder.yml",
        "desktop/electron/smoke-check.cjs",
        "desktop/packaging/pyinstaller_backend.spec",
        "desktop/packaging/pyinstaller_desktop_launcher.spec",
        "desktop/packaging/build_backend.ps1",
        "desktop/packaging/write_gpu_runtime_manifest.py",
        "desktop/packaging/probe_backend_gpu_runtime.py",
        "desktop/packaging/build_launcher.ps1",
        "desktop/packaging/build_frontend.ps1",
        "desktop/packaging/build_desktop.ps1",
        "desktop/packaging/build_all_windows.ps1",
        "src/backend/app/desktop_backend_entry.py",
        "src/backend/app/desktop_launcher_entry.py",
        "docs/桌面与前端/桌面应用打包.md",
    ]

    for relative in required:
        assert (ROOT / relative).exists(), relative


def test_electron_main_contract():
    main = read("desktop/electron/main.cjs")

    assert "medimage-backend.exe" in main
    assert "findAvailablePort" in main
    assert 'HEALTH_PATH = "/api/health"' in main
    assert "MEDIMAGE_DESKTOP_SMOKE" in main
    assert "MEDIMAGE_DESKTOP_SMOKE_RESULT" in main
    assert "verifyFrontendRenderer" in main
    assert "reactRootChildCount" in main
    assert "mainLandmarkPresent" in main
    assert "rendererConsoleErrors" in main
    assert "rendererBackendHealthOk" in main
    assert 'fetch(backendBaseUrl + "/api/health")' in main
    assert "MEDIMAGE_DESKTOP_USER_DATA" in main
    assert "MEDIMAGE_DESKTOP_WORKSPACE" in main
    assert "findRepositoryRoot" in main
    assert "resolveDefaultDataRoot" in main
    assert 'path.join(repositoryRoot, "workspace")' in main
    assert 'path.join(getUserWorkspace(), ".runtime", "backend-sidecar")' in main
    assert "MEDIMAGE_DESKTOP_API_BASE_URL" in main
    assert "loadFile(frontendIndex)" in main
    assert "medimage-backend.bin" in main
    assert "copyFileSync" in main
    assert "DEFAULT_BACKEND_STARTUP_TIMEOUT_MS = 120_000" in main
    assert "MEDIMAGE_DESKTOP_BACKEND_STARTUP_TIMEOUT_MS" in main
    assert "Date.now() < deadline" in main
    assert '["error", "stopped"].includes(backendState.status)' in main
    assert "spawnSync" in main
    assert '"taskkill"' in main
    assert '"/t"' in main
    assert '"/f"' in main
    assert "backendProcess.kill()" in main
    assert "requestSingleInstanceLock" in main
    assert 'app.on("second-instance"' in main
    assert "mainWindow.focus()" in main
    assert "payloadKey" in main
    assert "app.getVersion()" in main
    assert "deferred stale sidecar cleanup" in main
    assert "fs.rmSync(destinationDir" not in main
    assert "startBackendOnce" in main
    assert "backendStartPromise" in main
    assert "MEDIMAGE_DESKTOP_SESSION_TOKEN" in main
    assert "X-MedImage-Desktop-Health-Nonce" in main
    assert "timingSafeEqual" in main
    assert 'MEDIMAGE_DESKTOP_PARENT_PID: String(process.pid)' in main
    assert 'MEDIMAGE_AGENT_STARTUP_RECONCILE: "1"' in main
    assert "contextIsolation: true" in main
    assert "nodeIntegration: false" in main
    assert "sandbox: true" in main
    assert "--no-sandbox" not in main
    assert "setWindowOpenHandler" in main
    assert "resolveDcm2niixPath" not in main
    assert "MEDIMAGE_DCM2NIIX_PATH" not in main
    assert "MEDIMAGE_ENABLE_DICOM_CONVERSION" not in main
    assert "MEDIMAGE_ENABLE_REVIEWED_EXECUTION" not in main
    assert "MEDIMAGE_ALLOW_USER_DATA_CONVERSION" not in main
    assert "MEDIMAGE_ALLOW_PUBLIC_DICOM_CONVERSION_ENDPOINT" not in main
    assert "MEDIMAGE_ENABLE_SYNTHETIC_DICOM_SMOKE" not in main
    assert "MEDIMAGE_ALLOW_EXTERNAL_TOOL_SMOKE" not in main
    assert "MEDIMAGE_ALLOW_PERSISTED_SYNTHETIC_CONVERSION" not in main
    assert "MEDIMAGE_ALLOW_REAL_DCM2NIIX_SMOKE" not in main
    assert "MEDIMAGE_ALLOW_INTERNAL_USER_DICOM_CONVERSION_PROTOTYPE" not in main
    assert "VITE_ENABLE_DICOM_EXECUTE_UI" not in main
    assert "MEDIMAGE_FRONTEND_DICOM_EXECUTE_UI_ENABLED" not in main
    assert "MEDIMAGE_MATLAB_ENABLED" not in main
    assert "MEDIMAGE_SPM_SMOKE_ENABLED" not in main
    assert "inference" not in main.lower()
    assert "safetensors" not in main.lower()


def test_preload_exposes_runtime_config_without_raw_ipc():
    preload = read("desktop/electron/preload.cjs")

    assert "MEDIMAGE_API_BASE_URL" in preload
    assert "__MEDIMAGE_DESKTOP_CONFIG__" in preload
    assert "backendBaseUrl" in preload
    assert "getBackendBaseUrl" in preload
    assert 'exposeInMainWorld("ipcRenderer"' not in preload


def test_electron_builder_contract():
    builder = read("desktop/electron/electron-builder.yml")

    assert "../../src/frontend/dist" in builder
    assert "../packaging/dist/backend_payload" in builder
    assert "../resources/tools" not in builder
    assert "../../matlab" not in builder
    assert "workspace_seed/matlab" not in builder
    assert "workspace_seed/examples" in builder
    assert "target: nsis" in builder
    assert "target: portable" in builder
    assert "signAndEditExecutable: false" in builder
    assert "oneClick: true" in builder
    assert "perMachine: false" in builder
    assert "runAfterFinish: false" in builder
    assert "allowToChangeInstallationDirectory" not in builder
    assert "rawdata" not in builder
    assert "data/DemoData" not in builder


def test_desktop_package_contract():
    package_json = json.loads(read("desktop/electron/package.json"))

    assert package_json["main"] == "main.cjs"
    assert package_json["scripts"]["dist"] == "node build-dist.cjs"
    assert package_json["scripts"]["dist:dir"] == "node build-dist.cjs --win dir"
    assert "electron" in package_json["devDependencies"]
    assert "electron-builder" in package_json["devDependencies"]


def test_pyinstaller_spec_excludes_blocked_model_modules():
    spec = read("desktop/packaging/pyinstaller_backend.spec")
    launcher_spec = read("desktop/packaging/pyinstaller_desktop_launcher.spec")

    assert "desktop_backend_entry.py" in spec
    assert "medimage-backend" in spec
    assert "upx=False" in spec
    assert 'runtime_tmpdir="."' in spec
    assert '"scipy.ndimage"' in spec
    assert '"scipy.signal"' in spec
    assert 'collect_submodules("scipy")' not in spec
    assert 'collect_dynamic_libs("scipy")' in spec
    assert '"cudart64_12.dll"' in spec
    assert '"cublas64_12.dll"' in spec
    assert '"licenses/cuda"' in spec
    assert 'collect_submodules("cupy_backends")' in spec
    assert 'collect_submodules("cupy")' in spec
    assert 'collect_data_files("cupy", includes=["_core/include/**/*"])' in spec
    assert 'collect_submodules("fastrlock")' in spec
    assert '"graphlib"' in spec
    assert '"pydicom"' in spec
    assert "dcm2niix" not in spec.lower()
    assert "resources/tools" not in spec
    assert '"torch"' in spec
    assert '"safetensors"' in spec
    assert "desktop_launcher_entry.py" in launcher_spec
    assert "MedImage Agent" in launcher_spec
    assert "upx=False" in launcher_spec
    assert 'runtime_tmpdir="."' in launcher_spec
    assert '"src" / "frontend" / "dist"' in launcher_spec
    assert '"torch"' in launcher_spec
    assert '"safetensors"' in launcher_spec


def test_desktop_launcher_contract():
    launcher = read("src/backend/app/desktop_launcher_entry.py")

    assert "127.0.0.1" in launcher
    assert "find_available_port" in launcher
    assert "StaticFiles" in launcher
    assert "MEDIMAGE_DESKTOP_WORKSPACE" in launcher
    assert "_default_packaged_workspace" in launcher
    assert "_find_repository_root" in launcher
    assert "server.should_exit = True" in launcher
    assert "inference" not in launcher.lower()
    assert "safetensors" not in launcher.lower()


def test_frontend_runtime_config_contract():
    client = read("src/frontend/src/lib/api/client.ts")
    vite = read("src/frontend/vite.config.ts")
    build_frontend = read("desktop/packaging/build_frontend.ps1")

    assert "__MEDIMAGE_DESKTOP_CONFIG__" in client
    assert "MEDIMAGE_API_BASE_URL" in client
    assert 'base: "./"' in vite
    assert "VITE_ENABLE_DICOM_EXECUTE_UI" in build_frontend
    assert "Remove-Item Env:VITE_ENABLE_DICOM_EXECUTE_UI" in build_frontend


def test_backend_build_checks_native_scientific_dependencies():
    build_backend = read("desktop/packaging/build_backend.ps1")
    requirements = read("requirements.txt")

    assert "scipy.ndimage" in build_backend
    assert "scipy.signal" in build_backend
    assert "graphlib" in build_backend
    assert "pydicom" in build_backend
    assert "pydicom>=2.4.0,<4.0.0" in requirements
    assert "Scientific packaging dependency check failed" in build_backend
    assert "GpuManifestScript" in build_backend
    assert "GpuRuntimeProbeScript" in build_backend
    assert "AnalysisTocPath" in build_backend
    assert "GPU runtime manifest" in build_backend
    assert "CUPY_CACHE_DIR" in build_backend
    assert "cupy_cache" in build_backend
    assert "Remove-Item Env:CUPY_CACHE_DIR" in build_backend
    assert "gpu_runtime_probe.json" in build_backend
    assert "--expect-cupy" in build_backend


def test_gpu_runtime_manifest_is_portable_and_inventory_based():
    manifest_writer = read("desktop/packaging/write_gpu_runtime_manifest.py")

    assert "gpu_runtime_manifest.json" in manifest_writer
    assert "bundled_gpu_dlls" in manifest_writer
    assert "cuda_driver_requirement" in manifest_writer
    assert 'rglob("*.dll")' in manifest_writer
    assert "analysis_toc" in manifest_writer


def test_packaged_gpu_probe_launches_frozen_backend_and_checks_lazy_import():
    probe = read("desktop/packaging/probe_backend_gpu_runtime.py")

    assert "/api/health" in probe
    assert "/api/gpu/detect" in probe
    assert "/api/desktop/capabilities/dicom-conversion" in probe
    assert "MEDIMAGE_DESKTOP_WORKSPACE" in probe
    assert "CUPY_CACHE_DIR" in probe
    assert "cupy_available" in probe
    assert "dicom_converter_available" in probe
    assert "dicom_execution_supported" in probe
    assert "_stop_backend_process_tree" in probe
    assert '["taskkill", "/pid", str(proc.pid), "/t", "/f"]' in probe


def test_desktop_dist_wrapper_uses_workspace_caches():
    wrapper = read("desktop/electron/build-dist.cjs")
    build_desktop = read("desktop/packaging/build_desktop.ps1")
    build_all = read("desktop/packaging/build_all_windows.ps1")

    assert "ELECTRON_CACHE" in wrapper
    assert ".electron-cache" in wrapper
    assert "ELECTRON_BUILDER_CACHE" in wrapper
    assert ".electron-builder-cache" in wrapper
    assert "TEMP: tempRoot" in wrapper
    assert "TMP: tempRoot" in wrapper
    assert ".tmp" in wrapper
    assert "NPM_CONFIG_CACHE" in wrapper
    assert ".npm-cache" in wrapper
    assert "frontendBuilder" in wrapper
    assert "MEDIMAGE_ELECTRON_RUNTIME_ZIP" in wrapper
    assert "MEDIMAGE_ELECTRON_NSIS_ARCHIVE" in wrapper
    assert "MEDIMAGE_ELECTRON_NSIS_RESOURCES_ARCHIVE" in wrapper
    assert "ELECTRON_BUILDER_NSIS_DIR" in wrapper
    assert "ELECTRON_BUILDER_BINARIES_DOWNLOAD_OVERRIDE_URL" in wrapper
    assert "manual-runtime" in wrapper
    assert "manual-nsis" in wrapper
    assert "manual-binaries" in wrapper
    assert "--config.electronDist" in wrapper
    assert "ensureBackendPayload" in wrapper
    assert "Backend sidecar payload is required" in wrapper
    assert "medimage-backend.exe" in wrapper
    assert "medimage-backend.bin" in wrapper
    assert "ElectronRuntimeZip" in build_desktop
    assert "ElectronRuntimeZip" in build_all
    assert "NsisArchive" in build_desktop
    assert "NsisArchive" in build_all
    assert "NsisResourcesArchive" in build_desktop
    assert "NsisResourcesArchive" in build_all
    assert "Clear-PackagingResiduals" in build_all
    assert '".pytest_*"' in build_all
    assert '"_MEI*"' in build_all
    assert "Unable to remove generated packaging/test residual directories" in build_all
    assert "Processes that may hold locks" in build_all
    assert "Unable to inspect local process list" in build_all
    assert "Close running pytest, Python, PyInstaller, or MedImage Agent processes" in build_all
    assert "$TestPython = if ($PythonExe)" in build_all
    assert "Invoke-PytestChecked" in build_all
    assert "--basetemp=.pytest_tmp_packaging_focused" in build_all
    assert "Focused packaging tests" in build_all
    assert "DirOnly" in build_desktop
    assert "DirOnly" in build_all
    assert "LASTEXITCODE" in build_desktop
    assert "Copy-Item" in build_desktop
    assert "backend_payload" in build_desktop
    assert "build_frontend.ps1" in build_desktop
    assert "-ExecutionPolicy Bypass -File $FrontendBuildScript" in build_desktop


def test_desktop_docs_record_safety_boundaries():
    docs = read("docs/桌面与前端/桌面应用打包.md")

    required = [
        "does not connect a real model",
        "does not call inference",
        "does not load model weights",
        "does not change the reviewed execution allowlist",
    ]
    for phrase in required:
        assert phrase in docs

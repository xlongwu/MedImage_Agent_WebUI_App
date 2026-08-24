const fs = require("node:fs");
const path = require("node:path");

const root = path.resolve(__dirname, "..", "..");
const electronRoot = __dirname;
const checks = [];

function check(name, ok, detail = "") {
  checks.push({ name, ok, detail });
}

function read(filePath) {
  return fs.readFileSync(filePath, "utf8");
}

function exists(rel) {
  return fs.existsSync(path.join(root, rel));
}

const packageJson = JSON.parse(read(path.join(electronRoot, "package.json")));
const main = read(path.join(electronRoot, "main.cjs"));
const preload = read(path.join(electronRoot, "preload.cjs"));
const builder = read(path.join(electronRoot, "electron-builder.yml"));

check("desktop package main points to Electron main", packageJson.main === "main.cjs");
check("desktop package has Electron dependency", !!packageJson.devDependencies?.electron);
check("desktop package has electron-builder dependency", !!packageJson.devDependencies?.["electron-builder"]);
check("desktop package has dir-only dist script", packageJson.scripts?.["dist:dir"] === "node build-dist.cjs --win dir");
check("PyInstaller backend entry exists", exists("src/backend/app/desktop_backend_entry.py"));
check("PyInstaller launcher entry exists", exists("src/backend/app/desktop_launcher_entry.py"));
check("PyInstaller spec exists", exists("desktop/packaging/pyinstaller_backend.spec"));
check("PyInstaller launcher spec exists", exists("desktop/packaging/pyinstaller_desktop_launcher.spec"));
check("frontend build script exists", exists("desktop/packaging/build_frontend.ps1"));
check("backend build script exists", exists("desktop/packaging/build_backend.ps1"));
check("launcher build script exists", exists("desktop/packaging/build_launcher.ps1"));
check("desktop build script exists", exists("desktop/packaging/build_desktop.ps1"));
check("all-in-one Windows build script exists", exists("desktop/packaging/build_all_windows.ps1"));
check("documentation exists", exists("docs/桌面与前端/桌面应用打包.md"));
check("desktop dist wrapper exists", fs.existsSync(path.join(electronRoot, "build-dist.cjs")));

check("main resolves PyInstaller sidecar", main.includes("medimage-backend.exe") && main.includes("resolveBackendCommand"));
check("main can prepare backend payload sidecar", main.includes("medimage-backend.bin") && main.includes("copyFileSync"));
check("main starts backend sidecar", main.includes("spawn(backend.command, backend.args"));
check(
  "main binds the sidecar lifecycle to the Electron parent",
  main.includes('MEDIMAGE_DESKTOP_PARENT_PID: String(process.pid)')
);
check("main chooses available localhost port", main.includes("findAvailablePort") && main.includes("net.createServer"));
check("main probes /api/health before UI", main.includes('HEALTH_PATH = "/api/health"') && main.includes("waitForBackend"));
check(
  "main verifies a challenge-response proof from the sidecar health endpoint",
  main.includes("MEDIMAGE_DESKTOP_SESSION_TOKEN") &&
    main.includes("X-MedImage-Desktop-Health-Nonce") &&
    main.includes("timingSafeEqual")
);
check(
  "main allows packaged backend cold starts to exceed 30 seconds",
  main.includes("DEFAULT_BACKEND_STARTUP_TIMEOUT_MS = 120_000") &&
    main.includes("MEDIMAGE_DESKTOP_BACKEND_STARTUP_TIMEOUT_MS") &&
    main.includes("Date.now() < deadline")
);
check("main injects runtime backend URL", main.includes("MEDIMAGE_DESKTOP_API_BASE_URL") && main.includes("syncRuntimeEnv"));
check("main loads local static frontend", main.includes("loadFile(frontendIndex)") && main.includes("src\", \"frontend\", \"dist"));
check(
  "main copies workspace seeds without traversing restricted parent paths",
  main.includes("copySeedDirectory") &&
    main.includes("lstatSync") &&
    main.includes("isSymbolicLink") &&
    !main.includes("fs.cpSync")
);
check("main stops managed backend on quit", main.includes("backendProcess.kill()") && main.includes('app.on("before-quit"'));
check(
  "main enforces one desktop instance",
  main.includes("requestSingleInstanceLock") &&
    main.includes('app.on("second-instance"') &&
    main.includes("mainWindow.focus()")
);
check(
  "main deploys backend payload into a versioned directory",
  main.includes("payloadKey") &&
    main.includes("app.getVersion()") &&
    main.includes("deferred stale sidecar cleanup") &&
    !main.includes("fs.rmSync(destinationDir")
);
check(
  "main cleans managed backend after startup failure",
  main.includes("createWindow().catch((error) => {") &&
    main.includes("stopBackend();")
);
check("main supports hidden smoke run", main.includes("MEDIMAGE_DESKTOP_SMOKE") && main.includes("MEDIMAGE_DESKTOP_SMOKE_RESULT"));
check(
  "main verifies the mounted React renderer during smoke",
  main.includes("verifyFrontendRenderer") &&
    main.includes("reactRootChildCount") &&
    main.includes("mainLandmarkPresent") &&
    main.includes("rendererConsoleErrors")
);
check(
  "main verifies renderer-to-backend HTTP integration during smoke",
  main.includes("rendererBackendHealthOk") &&
    main.includes('fetch(backendBaseUrl + "/api/health")')
);
check(
  "main supports an isolated visible Agent-first navigation smoke",
  main.includes("MEDIMAGE_DESKTOP_VISIBLE_SMOKE") &&
    main.includes("verifyAgentFirstNavigation") &&
    main.includes("verifyBidsToFcWorkflow") &&
    main.includes("navigationCount") &&
    main.includes("visited")
);
check(
  "main records exact-SHA build provenance and visible workflow evidence",
  main.includes("build-provenance.json") &&
    main.includes("buildProvenance") &&
    main.includes("MEDIMAGE_DESKTOP_SMOKE_SCREENSHOT") &&
    main.includes("captureSmokeScreenshot")
);
check("main denies new windows", main.includes("setWindowOpenHandler") && main.includes('action: "deny"'));
check("main restricts dev URLs to localhost", main.includes("isAllowedDevUrl") && main.includes("127.0.0.1"));
check(
  "main keeps execution gates disabled by default",
  !main.includes("resolveDcm2niixPath") &&
    !main.includes("MEDIMAGE_DCM2NIIX_PATH") &&
    !main.includes("MEDIMAGE_ENABLE_DICOM_CONVERSION") &&
    !main.includes("MEDIMAGE_ENABLE_REVIEWED_EXECUTION") &&
    !main.includes("MEDIMAGE_ALLOW_USER_DATA_CONVERSION") &&
    !main.includes("MEDIMAGE_ALLOW_PUBLIC_DICOM_CONVERSION_ENDPOINT") &&
    !main.includes("MEDIMAGE_ENABLE_SYNTHETIC_DICOM_SMOKE") &&
    !main.includes("MEDIMAGE_ALLOW_EXTERNAL_TOOL_SMOKE") &&
    !main.includes("MEDIMAGE_ALLOW_PERSISTED_SYNTHETIC_CONVERSION") &&
    !main.includes("MEDIMAGE_ALLOW_REAL_DCM2NIIX_SMOKE") &&
    !main.includes("MEDIMAGE_ALLOW_INTERNAL_USER_DICOM_CONVERSION_PROTOTYPE") &&
    !main.includes("VITE_ENABLE_DICOM_EXECUTE_UI") &&
    !main.includes("MEDIMAGE_FRONTEND_DICOM_EXECUTE_UI_ENABLED") &&
    !main.includes("MEDIMAGE_MATLAB_ENABLED") &&
    !main.includes("MEDIMAGE_SPM_SMOKE_ENABLED")
);
check("main does not reference model inference", !/inference|weights|torch|safetensors/i.test(main));
check("BrowserWindow uses contextIsolation", main.includes("contextIsolation: true"));
check("BrowserWindow disables nodeIntegration", main.includes("nodeIntegration: false"));
check("BrowserWindow enables sandbox", main.includes("sandbox: true"));
check("desktop main never disables Chromium sandbox", !main.includes("--no-sandbox"));

check("preload exposes sync backend URL", preload.includes("MEDIMAGE_API_BASE_URL"));
check("preload exposes desktop config", preload.includes("__MEDIMAGE_DESKTOP_CONFIG__") && preload.includes("backendBaseUrl"));
check("preload exposes minimal IPC bridge", preload.includes("getBackendBaseUrl") && !preload.includes("exposeInMainWorld(\"ipcRenderer\""));

check("builder includes frontend static dist", builder.includes("../../src/frontend/dist"));
check("builder includes backend sidecar payload resource", builder.includes("../packaging/dist/backend_payload"));
check(
  "builder includes exact-SHA build provenance",
  builder.includes("../packaging/dist/release_metadata/build-provenance.json") &&
    builder.includes("release/build-provenance.json")
);
check("builder excludes external converter resources", !builder.includes("../resources/tools"));
check("builder excludes MATLAB workspace resources", !builder.includes("../../matlab") && !builder.includes("workspace_seed/matlab"));
check("builder targets Windows installer", builder.includes("target: nsis"));
check("builder targets portable exe", builder.includes("target: portable"));
check("builder skips Windows signing helper download", builder.includes("signAndEditExecutable: false"));
check("builder does not bundle rawdata", !/rawdata|data\/DemoData/.test(builder));
check("builder skips npm rebuild", builder.includes("npmRebuild: false"));

const buildDist = read(path.join(electronRoot, "build-dist.cjs"));
check("dist wrapper sets Electron cache in workspace", buildDist.includes("ELECTRON_CACHE") && buildDist.includes(".electron-cache"));
check("dist wrapper sets electron-builder cache in workspace", buildDist.includes("ELECTRON_BUILDER_CACHE") && buildDist.includes(".electron-builder-cache"));
check("dist wrapper sets temp in workspace", buildDist.includes("TEMP: tempRoot") && buildDist.includes("TMP: tempRoot") && buildDist.includes(".tmp"));
check("dist wrapper sets npm cache in workspace", buildDist.includes("NPM_CONFIG_CACHE") && buildDist.includes(".npm-cache"));
check("dist wrapper can reuse frontend electron-builder", buildDist.includes("src\", \"frontend") && buildDist.includes("frontendBuilder"));
check(
  "dist wrapper supports local Electron runtime zip",
  buildDist.includes("MEDIMAGE_ELECTRON_RUNTIME_ZIP") &&
    buildDist.includes("manual-runtime") &&
    buildDist.includes("--config.electronDist")
);
check(
  "dist wrapper supports local NSIS archive",
  buildDist.includes("MEDIMAGE_ELECTRON_NSIS_ARCHIVE") &&
    buildDist.includes("manual-nsis") &&
    buildDist.includes("ELECTRON_BUILDER_NSIS_DIR")
);
check(
  "dist wrapper supports local NSIS resources archive",
  buildDist.includes("MEDIMAGE_ELECTRON_NSIS_RESOURCES_ARCHIVE") &&
    buildDist.includes("manual-binaries") &&
    buildDist.includes("ELECTRON_BUILDER_BINARIES_DOWNLOAD_OVERRIDE_URL")
);
check(
  "dist wrapper requires a non-empty backend sidecar payload",
  buildDist.includes("ensureBackendPayload") &&
    buildDist.includes("medimage-backend.exe") &&
    buildDist.includes("medimage-backend.bin") &&
    buildDist.includes("Backend sidecar payload is required")
);

const ok = checks.every((item) => item.ok);
console.log(JSON.stringify({ ok, checks }, null, 2));
process.exit(ok ? 0 : 1);

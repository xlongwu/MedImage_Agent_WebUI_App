const { app, BrowserWindow, dialog, ipcMain, shell } = require("electron");
const { spawn, spawnSync } = require("node:child_process");
const crypto = require("node:crypto");
const fs = require("node:fs");
const http = require("node:http");
const net = require("node:net");
const path = require("node:path");

const API_HOST = "127.0.0.1";
const DEFAULT_API_PORT = Number(process.env.MEDIMAGE_DESKTOP_BACKEND_PORT || "8765");
const BACKEND_EXE_NAME = "medimage-backend.exe";
const BACKEND_PAYLOAD_NAME = "medimage-backend.bin";
const HEALTH_PATH = "/api/health";
const DESKTOP_HEALTH_NONCE_HEADER = "X-MedImage-Desktop-Health-Nonce";
const DESKTOP_HEALTH_PROOF_HEADER = "x-medimage-desktop-health-proof";
const DEFAULT_BACKEND_STARTUP_TIMEOUT_MS = 120_000;
const configuredBackendStartupTimeoutMs = Number(
  process.env.MEDIMAGE_DESKTOP_BACKEND_STARTUP_TIMEOUT_MS
);
const BACKEND_STARTUP_TIMEOUT_MS =
  Number.isFinite(configuredBackendStartupTimeoutMs) && configuredBackendStartupTimeoutMs > 0
    ? configuredBackendStartupTimeoutMs
    : DEFAULT_BACKEND_STARTUP_TIMEOUT_MS;
const BACKEND_HEALTH_POLL_INTERVAL_MS = 500;
const IS_SMOKE_TEST = process.env.MEDIMAGE_DESKTOP_SMOKE === "1";

function findRepositoryRoot(startPath) {
  let current = path.resolve(startPath);
  while (true) {
    if (
      fs.existsSync(path.join(current, "pyproject.toml")) &&
      fs.existsSync(path.join(current, "desktop", "electron"))
    ) {
      return current;
    }
    const parent = path.dirname(current);
    if (parent === current) {
      return null;
    }
    current = parent;
  }
}

function resolveDefaultDataRoot() {
  const anchor = app.isPackaged ? path.dirname(process.execPath) : __dirname;
  const repositoryRoot = findRepositoryRoot(anchor);
  return repositoryRoot
    ? path.join(repositoryRoot, "workspace")
    : path.join(path.dirname(process.execPath), "workspace");
}

const DEFAULT_DATA_ROOT = resolveDefaultDataRoot();
const configuredUserData = process.env.MEDIMAGE_DESKTOP_USER_DATA;
app.setPath(
  "userData",
  configuredUserData
    ? path.resolve(configuredUserData)
    : path.join(DEFAULT_DATA_ROOT, ".desktop")
);

let backendProcess = null;
let backendStartPromise = null;
let backendStopping = false;
let mainWindow = null;
let agentApprovalToken = null;
let backendSessionToken = null;
let backendState = {
  apiBaseUrl: `http://${API_HOST}:${DEFAULT_API_PORT}`,
  managed: false,
  ready: false,
  status: "pending",
  pid: null,
  logPath: "",
  error: null,
  executablePath: "",
  port: DEFAULT_API_PORT,
};

function delay(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function getRepoRoot() {
  return path.resolve(__dirname, "..", "..");
}

function getResourcesRoot() {
  return app.isPackaged ? process.resourcesPath : getRepoRoot();
}

function getUserWorkspace() {
  if (process.env.MEDIMAGE_DESKTOP_WORKSPACE) {
    return path.resolve(process.env.MEDIMAGE_DESKTOP_WORKSPACE);
  }
  if (configuredUserData) {
    return path.join(app.getPath("userData"), "workspace");
  }
  return DEFAULT_DATA_ROOT;
}

function getLogPath() {
  const base = path.join(getUserWorkspace(), "logs", "desktop");
  fs.mkdirSync(base, { recursive: true });
  return path.join(base, "backend-sidecar.log");
}

function appendBackendLog(channel, chunk) {
  try {
    if (!backendState.logPath) {
      backendState.logPath = getLogPath();
    }
    fs.appendFileSync(
      backendState.logPath,
      `[${new Date().toISOString()}] ${channel}: ${chunk.toString()}`,
      "utf8"
    );
  } catch {
    // Logging should never keep the desktop shell from exiting or opening.
  }
}

function copySeedDirectory(source, destination) {
  if (!fs.existsSync(source) || fs.existsSync(destination)) {
    return;
  }
  fs.mkdirSync(path.dirname(destination), { recursive: true });
  fs.cpSync(source, destination, { recursive: true });
}

function ensureDesktopWorkspace() {
  const workspace = getUserWorkspace();
  fs.mkdirSync(workspace, { recursive: true });
  process.env.MEDIMAGE_DESKTOP_WORKSPACE = workspace;

  if (app.isPackaged) {
    const seedRoot = path.join(getResourcesRoot(), "workspace_seed");
    copySeedDirectory(path.join(seedRoot, "examples"), path.join(workspace, "examples"));
    copySeedDirectory(path.join(seedRoot, "docs"), path.join(workspace, "docs"));
  }

  fs.mkdirSync(path.join(workspace, "outputs"), { recursive: true });
  return workspace;
}

function syncRuntimeEnv() {
  process.env.MEDIMAGE_API_BASE_URL = backendState.apiBaseUrl;
  process.env.MEDIMAGE_DESKTOP_API_BASE_URL = backendState.apiBaseUrl;
  process.env.MEDIMAGE_DESKTOP_BACKEND_MANAGED = String(backendState.managed);
  process.env.MEDIMAGE_DESKTOP_BACKEND_READY = String(backendState.ready);
  process.env.MEDIMAGE_DESKTOP_BACKEND_STATUS = backendState.status;
  process.env.MEDIMAGE_DESKTOP_BACKEND_LOG_PATH = backendState.logPath || "";
  process.env.MEDIMAGE_DESKTOP_BACKEND_PID = backendState.pid ? String(backendState.pid) : "";
  process.env.MEDIMAGE_DESKTOP_BACKEND_EXE = backendState.executablePath || "";
  process.env.MEDIMAGE_DESKTOP_BACKEND_PORT = String(backendState.port || DEFAULT_API_PORT);
}

function hasExpectedHealthProof(proof, nonce) {
  if (!backendSessionToken || typeof proof !== "string") {
    return false;
  }
  const expected = crypto
    .createHmac("sha256", backendSessionToken)
    .update(nonce, "utf8")
    .digest("hex");
  const expectedBuffer = Buffer.from(expected, "utf8");
  const proofBuffer = Buffer.from(proof, "utf8");
  return (
    expectedBuffer.length === proofBuffer.length &&
    crypto.timingSafeEqual(expectedBuffer, proofBuffer)
  );
}

function requestHealth(apiBaseUrl = backendState.apiBaseUrl, timeoutMs = 600) {
  return new Promise((resolve) => {
    if (!backendSessionToken) {
      resolve({ ok: false, error: "missing desktop sidecar session token" });
      return;
    }
    let done = false;
    const nonce = crypto.randomBytes(32).toString("hex");
    const finish = (result) => {
      if (!done) {
        done = true;
        resolve(result);
      }
    };
    const req = http.get(`${apiBaseUrl}${HEALTH_PATH}`, {
      headers: { [DESKTOP_HEALTH_NONCE_HEADER]: nonce },
    }, (res) => {
      res.resume();
      finish({
        ok:
          res.statusCode >= 200 &&
          res.statusCode < 300 &&
          hasExpectedHealthProof(res.headers[DESKTOP_HEALTH_PROOF_HEADER], nonce),
        statusCode: res.statusCode,
      });
    });
    req.on("error", (error) => finish({ ok: false, error: error.message }));
    req.setTimeout(timeoutMs, () => req.destroy(new Error("timeout")));
  });
}

function isPortFree(port) {
  return new Promise((resolve) => {
    const server = net.createServer();
    server.once("error", () => resolve(false));
    server.once("listening", () => {
      server.close(() => resolve(true));
    });
    server.listen(port, API_HOST);
  });
}

async function findAvailablePort(startPort = DEFAULT_API_PORT) {
  for (let offset = 0; offset < 40; offset += 1) {
    const port = startPort + offset;
    if (await isPortFree(port)) {
      return port;
    }
  }
  throw new Error(`No available backend port from ${startPort} to ${startPort + 39}.`);
}

function resolveFrontendIndex() {
  if (process.env.MEDIMAGE_DESKTOP_FRONTEND_INDEX) {
    return process.env.MEDIMAGE_DESKTOP_FRONTEND_INDEX;
  }
  return app.isPackaged
    ? path.join(getResourcesRoot(), "frontend", "index.html")
    : path.join(getRepoRoot(), "src", "frontend", "dist", "index.html");
}

function cleanupStaleBackendPayloads(destinationRoot, keepDir) {
  if (!fs.existsSync(destinationRoot)) {
    return;
  }
  for (const entry of fs.readdirSync(destinationRoot, { withFileTypes: true })) {
    const candidate = path.join(destinationRoot, entry.name);
    if (candidate === keepDir) {
      continue;
    }
    try {
      fs.rmSync(candidate, { recursive: entry.isDirectory(), force: true });
    } catch (error) {
      appendBackendLog(
        "desktop",
        `deferred stale sidecar cleanup: ${candidate} (${error.code || error.message})\n`
      );
    }
  }
}

function ensureBackendFromPayload(backendResourceDir) {
  const payloadPath = path.join(backendResourceDir, BACKEND_PAYLOAD_NAME);
  if (!fs.existsSync(payloadPath)) {
    return null;
  }

  const payloadStat = fs.statSync(payloadPath);
  const destinationRoot = path.join(getUserWorkspace(), ".runtime", "backend-sidecar");
  const payloadKey = [
    app.getVersion().replace(/[^a-zA-Z0-9._-]/g, "_"),
    String(payloadStat.size),
    String(Math.trunc(payloadStat.mtimeMs)),
  ].join("-");
  let destinationDir = path.join(destinationRoot, payloadKey);
  const executablePath = path.join(destinationDir, BACKEND_EXE_NAME);
  const stampPath = path.join(destinationDir, ".backend-payload.json");
  const stamp = JSON.stringify({
    payload: BACKEND_PAYLOAD_NAME,
    appVersion: app.getVersion(),
    size: payloadStat.size,
    mtimeMs: payloadStat.mtimeMs,
  });

  if (fs.existsSync(executablePath) && fs.existsSync(stampPath)) {
    try {
      if (fs.readFileSync(stampPath, "utf8") === stamp) {
        cleanupStaleBackendPayloads(destinationRoot, destinationDir);
        return executablePath;
      }
    } catch {
      // Fall through and re-extract.
    }
  }

  if (fs.existsSync(destinationDir)) {
    destinationDir = path.join(destinationRoot, `${payloadKey}-${process.pid}-${Date.now()}`);
  }
  const preparedExecutablePath = path.join(destinationDir, BACKEND_EXE_NAME);
  const preparedStampPath = path.join(destinationDir, ".backend-payload.json");
  fs.mkdirSync(destinationDir, { recursive: true });
  appendBackendLog("desktop", `preparing backend sidecar payload: ${payloadPath}\n`);
  fs.copyFileSync(payloadPath, preparedExecutablePath);
  if (!fs.existsSync(preparedExecutablePath)) {
    throw new Error(`Backend payload could not be copied to ${preparedExecutablePath}.`);
  }

  fs.writeFileSync(preparedStampPath, stamp, "utf8");
  cleanupStaleBackendPayloads(destinationRoot, destinationDir);
  return preparedExecutablePath;
}

function resolveBackendCommand(port) {
  const envExe = process.env.MEDIMAGE_DESKTOP_BACKEND_EXE;
  const packagedBackendDir = path.join(getResourcesRoot(), "backend");
  const packagedExe = path.join(packagedBackendDir, BACKEND_EXE_NAME);
  const payloadExe = app.isPackaged ? ensureBackendFromPayload(packagedBackendDir) : null;
  const devExe = path.join(getRepoRoot(), "desktop", "packaging", "dist", "backend", BACKEND_EXE_NAME);

  const executable = [envExe, packagedExe, payloadExe, devExe].find((candidate) => candidate && fs.existsSync(candidate));
  if (executable) {
    return {
      command: executable,
      args: ["--host", API_HOST, "--port", String(port)],
      executablePath: executable,
      cwd: ensureDesktopWorkspace(),
    };
  }

  if (app.isPackaged) {
    throw new Error(`Backend sidecar not found at ${packagedExe}.`);
  }

  const python = process.env.MEDIMAGE_PYTHON || "python";
  return {
    command: python,
    args: [
      "-m",
      "src.backend.app.desktop_backend_entry",
      "--host",
      API_HOST,
      "--port",
      String(port),
    ],
    executablePath: `${python} -m src.backend.app.desktop_backend_entry`,
    cwd: getRepoRoot(),
  };
}

async function waitForBackend(
  apiBaseUrl = backendState.apiBaseUrl,
  timeoutMs = BACKEND_STARTUP_TIMEOUT_MS
) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    const result = await requestHealth(apiBaseUrl);
    if (result.ok) {
      return true;
    }
    if (["error", "stopped"].includes(backendState.status)) {
      return false;
    }
    const remainingMs = deadline - Date.now();
    if (remainingMs > 0) {
      await delay(Math.min(BACKEND_HEALTH_POLL_INTERVAL_MS, remainingMs));
    }
  }
  return false;
}

async function startBackendOnce() {
  backendState = { ...backendState, logPath: getLogPath() };

  if (process.env.MEDIMAGE_DESKTOP_SKIP_BACKEND === "true") {
    backendState = { ...backendState, managed: false, ready: false, status: "skipped" };
    syncRuntimeEnv();
    return false;
  }

  const port = await findAvailablePort(DEFAULT_API_PORT);
  const apiBaseUrl = `http://${API_HOST}:${port}`;
  const backend = resolveBackendCommand(port);
  backendSessionToken = crypto.randomBytes(32).toString("hex");
  agentApprovalToken = crypto.randomBytes(32).toString("hex");
  const agentApprovalActor =
    process.env.USERNAME || process.env.USER || "desktop-local-user";
  backendState = {
    ...backendState,
    apiBaseUrl,
    managed: true,
    ready: false,
    status: "starting",
    pid: null,
    port,
    executablePath: backend.executablePath,
    error: null,
  };
  syncRuntimeEnv();
  appendBackendLog("desktop", `backend executable: ${backend.executablePath}\n`);
  appendBackendLog("desktop", `backend port: ${port}\n`);
  appendBackendLog("desktop", `backend startup timeout: ${BACKEND_STARTUP_TIMEOUT_MS} ms\n`);
  appendBackendLog("desktop", `frontend path: ${resolveFrontendIndex()}\n`);
  backendProcess = spawn(backend.command, backend.args, {
    cwd: backend.cwd,
    env: {
      ...process.env,
      MEDIMAGE_DESKTOP: "1",
      MEDIMAGE_DESKTOP_PARENT_PID: String(process.pid),
      MEDIMAGE_DESKTOP_BACKEND_HOST: API_HOST,
      MEDIMAGE_DESKTOP_BACKEND_PORT: String(port),
      MEDIMAGE_BACKEND_HOST: API_HOST,
      MEDIMAGE_BACKEND_PORT: String(port),
      MEDIMAGE_DESKTOP_SESSION_TOKEN: backendSessionToken,
      MEDIMAGE_AGENT_STARTUP_RECONCILE: "1",
      MEDIMAGE_AGENT_APPROVAL_TOKEN: agentApprovalToken,
      MEDIMAGE_AGENT_APPROVAL_ACTOR: agentApprovalActor,
      // Execution gates are never enabled by the desktop shell. Explicit
      // operator/test environment configuration remains authoritative.
    },
    stdio: "pipe",
    windowsHide: true,
  });

  backendState = { ...backendState, pid: backendProcess.pid || null };
  syncRuntimeEnv();

  backendProcess.stdout.on("data", (chunk) => appendBackendLog("stdout", chunk));
  backendProcess.stderr.on("data", (chunk) => appendBackendLog("stderr", chunk));
  backendProcess.on("error", (error) => {
    backendState = { ...backendState, ready: false, status: "error", error: error.message };
    syncRuntimeEnv();
    appendBackendLog("error", `${error.message}\n`);
  });
  backendProcess.on("exit", (code, signal) => {
    const expectedStop = backendStopping;
    backendStopping = false;
    backendState = {
      ...backendState,
      ready: false,
      status: "stopped",
      pid: null,
      error: expectedStop || code === 0 ? null : `backend exited code=${code} signal=${signal}`,
    };
    backendSessionToken = null;
    agentApprovalToken = null;
    syncRuntimeEnv();
    appendBackendLog("exit", `code=${code} signal=${signal}\n`);
  });

  const ready = await waitForBackend(apiBaseUrl);
  backendState = {
    ...backendState,
    ready,
    status: ready ? "started" : "health-timeout",
  };
  syncRuntimeEnv();
  appendBackendLog("desktop", `backend health status: ${backendState.status}\n`);
  return ready;
}

async function startBackend() {
  if (backendProcess && backendState.ready) {
    return true;
  }
  if (!backendStartPromise) {
    backendStartPromise = startBackendOnce().finally(() => {
      backendStartPromise = null;
    });
  }
  return backendStartPromise;
}

function stopBackend() {
  if (backendProcess && backendState.managed) {
    const pid = backendProcess.pid;
    appendBackendLog("desktop", "stopping backend sidecar\n");
    if (process.platform === "win32" && pid) {
      backendStopping = true;
      const result = spawnSync("taskkill", ["/pid", String(pid), "/t", "/f"], {
        stdio: "pipe",
        windowsHide: true,
      });
      appendBackendLog("taskkill", `status=${result.status} signal=${result.signal || ""}\n`);
      if (result.error) {
        appendBackendLog("taskkill error", `${result.error.message}\n`);
        backendProcess.kill();
      }
    } else {
      backendStopping = true;
      backendProcess.kill();
    }
    backendState = {
      ...backendState,
      ready: false,
      status: "stopping",
      pid: null,
    };
    syncRuntimeEnv();
    backendProcess = null;
  }
  agentApprovalToken = null;
  backendSessionToken = null;
}

function runtimeSnapshot() {
  return {
    apiBaseUrl: backendState.apiBaseUrl,
    platform: process.platform,
    backend: {
      managed: backendState.managed,
      ready: backendState.ready,
      status: backendState.status,
      pid: backendState.pid,
      logPath: backendState.logPath,
      executablePath: backendState.executablePath,
      port: backendState.port,
    },
  };
}

function writeSmokeResult(payload) {
  const resultPath = process.env.MEDIMAGE_DESKTOP_SMOKE_RESULT;
  if (!IS_SMOKE_TEST || !resultPath) {
    return;
  }
  fs.mkdirSync(path.dirname(resultPath), { recursive: true });
  fs.writeFileSync(
    resultPath,
    JSON.stringify({ ...runtimeSnapshot(), ...payload }, null, 2),
    "utf8"
  );
}

function registerIpcHandlers() {
  ipcMain.handle("medimage:get-api-base-url", () => backendState.apiBaseUrl);
  ipcMain.handle("medimage:get-runtime", () => runtimeSnapshot());
  ipcMain.handle("medimage:get-agent-approval-token", () =>
    backendState.ready && agentApprovalToken ? agentApprovalToken : null
  );
  ipcMain.handle("medimage:select-directory", async (event) => {
    const result = await dialog.showOpenDialog(BrowserWindow.fromWebContents(event.sender), {
      properties: ["openDirectory"],
    });
    return result.canceled ? null : result.filePaths[0] || null;
  });
  ipcMain.handle("medimage:select-file", async (event, filters) => {
    const result = await dialog.showOpenDialog(BrowserWindow.fromWebContents(event.sender), {
      properties: ["openFile"],
      filters: Array.isArray(filters) ? filters : undefined,
    });
    return result.canceled ? null : result.filePaths[0] || null;
  });
  ipcMain.handle("medimage:open-external-path", async (_event, targetPath) => {
    if (typeof targetPath !== "string" || !targetPath.trim()) {
      return false;
    }
    const result = await shell.openPath(targetPath);
    return result === "";
  });
}

function makeBackendErrorHtml() {
  const detail = [
    `Backend URL: ${backendState.apiBaseUrl}`,
    `Status: ${backendState.status}`,
    `Executable: ${backendState.executablePath || "not resolved"}`,
    `Log: ${backendState.logPath || "not available"}`,
    backendState.error ? `Error: ${backendState.error}` : "",
  ].filter(Boolean).join("\n");
  return `<!doctype html>
<html>
<head>
  <meta charset="utf-8" />
  <title>MedImage Agent backend startup failed</title>
  <style>
    body { font-family: Segoe UI, Arial, sans-serif; margin: 48px; color: #172033; }
    pre { padding: 16px; background: #f3f5f8; border: 1px solid #d9dee8; border-radius: 8px; white-space: pre-wrap; }
  </style>
</head>
<body>
  <h1>MedImage Agent could not start the local backend</h1>
  <p>The desktop shell did not load the UI because the local FastAPI sidecar did not pass its health check.</p>
  <pre>${detail.replace(/[&<>]/g, (char) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;" }[char]))}</pre>
</body>
</html>`;
}

function isAllowedDevUrl(url) {
  try {
    const parsed = new URL(url);
    return parsed.protocol === "http:" && ["127.0.0.1", "localhost"].includes(parsed.hostname);
  } catch {
    return false;
  }
}

async function loadFrontend(win) {
  const frontendOnlySmoke =
    IS_SMOKE_TEST && process.env.MEDIMAGE_DESKTOP_SKIP_BACKEND === "true";
  if (!backendState.ready && !frontendOnlySmoke) {
    await win.loadURL(`data:text/html;charset=utf-8,${encodeURIComponent(makeBackendErrorHtml())}`);
    return;
  }

  const devUrl = process.env.MEDIMAGE_DESKTOP_DEV_URL;
  if (devUrl) {
    if (!isAllowedDevUrl(devUrl)) {
      throw new Error("MEDIMAGE_DESKTOP_DEV_URL must be a localhost URL.");
    }
    await win.loadURL(devUrl);
    return;
  }

  const frontendIndex = resolveFrontendIndex();
  if (!fs.existsSync(frontendIndex)) {
    throw new Error(`Frontend build not found: ${frontendIndex}`);
  }
  await win.loadFile(frontendIndex);
}

async function verifyFrontendRenderer(win, attempts = 40) {
  for (let index = 0; index < attempts; index += 1) {
    const snapshot = await win.webContents.executeJavaScript(`(async () => {
      const root = document.getElementById("root");
      const main = document.querySelector("main, [role=main]");
      const backendBaseUrl =
        window.__MEDIMAGE_DESKTOP_CONFIG__?.backendBaseUrl ||
        window.MEDIMAGE_API_BASE_URL ||
        window.MEDIMAGE_DESKTOP_RUNTIME?.apiBaseUrl ||
        "";
      let rendererBackendHealthOk = false;
      let rendererBackendHealthStatus = null;
      if (backendBaseUrl) {
        try {
          const response = await fetch(backendBaseUrl + "/api/health");
          rendererBackendHealthStatus = response.status;
          rendererBackendHealthOk = response.ok;
        } catch {
          rendererBackendHealthOk = false;
        }
      }
      return {
        backendConfigPresent: Boolean(backendBaseUrl),
        documentReadyState: document.readyState,
        documentTitle: document.title,
        locationProtocol: window.location.protocol,
        navigationLandmarkCount: document.querySelectorAll("nav").length,
        reactRootChildCount: root?.childElementCount ?? 0,
        reactRootTextLength: root?.textContent?.trim().length ?? 0,
        rendererBackendHealthOk,
        rendererBackendHealthStatus,
        mainLandmarkPresent: Boolean(main),
      };
    })()`, true);
    if (
      snapshot.documentReadyState === "complete" &&
      snapshot.reactRootChildCount > 0 &&
      snapshot.reactRootTextLength > 0 &&
      snapshot.mainLandmarkPresent &&
      snapshot.backendConfigPresent &&
      snapshot.rendererBackendHealthOk
    ) {
      return snapshot;
    }
    await delay(250);
  }
  throw new Error("Frontend renderer did not mount a non-empty React application shell.");
}

async function createWindow() {
  if (mainWindow && !mainWindow.isDestroyed()) {
    if (mainWindow.isMinimized()) {
      mainWindow.restore();
    }
    mainWindow.show();
    mainWindow.focus();
    return mainWindow;
  }

  await startBackend();

  const win = new BrowserWindow({
    width: 1320,
    height: 860,
    minWidth: 1100,
    minHeight: 720,
    show: !IS_SMOKE_TEST,
    title: "MedImage Agent",
    webPreferences: {
      preload: path.join(__dirname, "preload.cjs"),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true,
      webSecurity: true,
    },
  });
  mainWindow = win;
  win.on("closed", () => {
    if (mainWindow === win) {
      mainWindow = null;
    }
  });

  const rendererConsoleErrors = [];
  let rendererExit = null;
  win.webContents.on("console-message", (_event, level, message, line, sourceId) => {
    if (level >= 3) {
      rendererConsoleErrors.push({ message, line, sourceId });
    }
  });
  win.webContents.on("render-process-gone", (_event, details) => {
    rendererExit = details;
  });

  win.webContents.setWindowOpenHandler(() => ({ action: "deny" }));
  win.webContents.on("will-navigate", (event, url) => {
    if (url.startsWith("file://") || url.startsWith("data:text/html") || isAllowedDevUrl(url)) {
      return;
    }
    event.preventDefault();
  });

  await loadFrontend(win);

  if (IS_SMOKE_TEST) {
    const renderer = await verifyFrontendRenderer(win);
    if (rendererExit) {
      throw new Error(`Frontend renderer exited during smoke verification: ${rendererExit.reason}`);
    }
    if (rendererConsoleErrors.length > 0) {
      throw new Error(
        `Frontend renderer emitted ${rendererConsoleErrors.length} console error(s): ${rendererConsoleErrors[0].message}`
      );
    }
    writeSmokeResult({
      frontendIndex: resolveFrontendIndex(),
      frontendLoaded: true,
      rendererVerified: true,
      renderer,
      rendererConsoleErrors,
    });
    app.quit();
  }

  return win;
}

const hasSingleInstanceLock = app.requestSingleInstanceLock();

if (!hasSingleInstanceLock) {
  app.quit();
} else {
  app.on("second-instance", () => {
    if (mainWindow && !mainWindow.isDestroyed()) {
      if (mainWindow.isMinimized()) {
        mainWindow.restore();
      }
      mainWindow.show();
      mainWindow.focus();
    }
  });

  app.whenReady().then(() => {
    registerIpcHandlers();
    createWindow().catch((error) => {
      stopBackend();
      backendState = { ...backendState, ready: false, status: "error", error: error.message };
      syncRuntimeEnv();
      appendBackendLog("desktop", `fatal startup error: ${error.stack || error.message}\n`);
      if (IS_SMOKE_TEST) {
        writeSmokeResult({ frontendLoaded: false, rendererVerified: false, error: error.message });
        app.quit();
        return;
      }
      dialog.showErrorBox("MedImage Agent startup failed", error.message);
    });
  });

  app.on("before-quit", stopBackend);

  app.on("window-all-closed", () => {
    stopBackend();
    if (process.platform !== "darwin") {
      app.quit();
    }
  });

  app.on("activate", () => {
    if (BrowserWindow.getAllWindows().length === 0) {
      createWindow().catch((error) => {
        stopBackend();
        appendBackendLog("desktop", `activate error: ${error.stack || error.message}\n`);
      });
    }
  });
}

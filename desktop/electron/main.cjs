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
const IS_VISIBLE_SMOKE_TEST =
  IS_SMOKE_TEST && process.env.MEDIMAGE_DESKTOP_VISIBLE_SMOKE === "1";

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
  const sourceStat = fs.lstatSync(source);
  if (sourceStat.isSymbolicLink()) {
    throw new Error(`Workspace seed must not contain symbolic links: ${source}`);
  }
  if (sourceStat.isDirectory()) {
    fs.mkdirSync(destination, { recursive: true });
    for (const entry of fs.readdirSync(source, { withFileTypes: true })) {
      copySeedDirectory(path.join(source, entry.name), path.join(destination, entry.name));
    }
    return;
  }
  if (sourceStat.isFile()) {
    fs.mkdirSync(path.dirname(destination), { recursive: true });
    fs.copyFileSync(source, destination);
  }
}

function ensureDesktopWorkspace() {
  const workspace = getUserWorkspace();
  fs.mkdirSync(workspace, { recursive: true });
  process.env.MEDIMAGE_DESKTOP_WORKSPACE = workspace;

  if (app.isPackaged && !IS_SMOKE_TEST) {
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
  const payloadExe = app.isPackaged && !envExe ? ensureBackendFromPayload(packagedBackendDir) : null;
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
    buildProvenance: readBuildProvenance(),
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

function resolveBuildProvenancePath() {
  return app.isPackaged
    ? path.join(process.resourcesPath, "release", "build-provenance.json")
    : path.resolve(
        __dirname,
        "..",
        "packaging",
        "dist",
        "release_metadata",
        "build-provenance.json"
      );
}

function readBuildProvenance() {
  const provenancePath = resolveBuildProvenancePath();
  try {
    const payload = JSON.parse(fs.readFileSync(provenancePath, "utf8"));
    if (!payload?.git?.sha || typeof payload.git.clean !== "boolean") {
      return { valid: false, error: "BUILD_PROVENANCE_INVALID" };
    }
    return { ...payload, valid: true };
  } catch (error) {
    return { valid: false, error: "BUILD_PROVENANCE_UNAVAILABLE" };
  }
}

async function captureSmokeScreenshot(win) {
  const screenshotPath = process.env.MEDIMAGE_DESKTOP_SMOKE_SCREENSHOT;
  if (!IS_VISIBLE_SMOKE_TEST || !screenshotPath) {
    return null;
  }
  const image = await win.webContents.capturePage();
  fs.mkdirSync(path.dirname(screenshotPath), { recursive: true });
  fs.writeFileSync(screenshotPath, image.toPNG());
  return path.basename(screenshotPath);
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

function requestBackendJson(method, pathname, payload, extraHeaders = {}) {
  return new Promise((resolve, reject) => {
    const body = payload === undefined ? "" : JSON.stringify(payload);
    const headers = body
      ? {
          "Content-Type": "application/json",
          "Content-Length": Buffer.byteLength(body),
        }
      : {};
    Object.assign(headers, extraHeaders);
    const request = http.request(
      {
        hostname: API_HOST,
        port: backendState.port,
        path: pathname,
        method,
        headers,
      },
      (response) => {
        let responseBody = "";
        response.setEncoding("utf8");
        response.on("data", (chunk) => {
          responseBody += chunk;
        });
        response.on("end", () => {
          if ((response.statusCode || 500) >= 400) {
            reject(new Error(`Smoke fixture request failed (${response.statusCode}): ${responseBody}`));
            return;
          }
          try {
            resolve(responseBody ? JSON.parse(responseBody) : {});
          } catch (error) {
            reject(error);
          }
        });
      }
    );
    request.on("error", reject);
    request.end(body || undefined);
  });
}

function writeJsonAtomically(targetPath, payload) {
  const temporaryPath = `${targetPath}.${process.pid}.tmp`;
  fs.writeFileSync(temporaryPath, JSON.stringify(payload, null, 2), "utf8");
  fs.renameSync(temporaryPath, targetPath);
}

function firstSubjectBold(subject) {
  for (const session of subject?.sessions || []) {
    for (const functional of session?.func || []) {
      if (functional?.bold) return functional;
    }
  }
  return null;
}

async function prepareRecoverySmokeFixture(project, atlasPath) {
  if (!project.dataset_index_path) {
    throw new Error("Recovery smoke requires a persisted dataset index.");
  }
  const datasetIndex = JSON.parse(fs.readFileSync(project.dataset_index_path, "utf8"));
  const subjects = Array.isArray(datasetIndex.subjects) ? datasetIndex.subjects : [];
  if (subjects.length !== 3) {
    throw new Error(`Recovery smoke requires exactly three subjects, found ${subjects.length}.`);
  }
  for (const subject of subjects) {
    subject.status = "COMPLETE";
    subject.issues = [];
    const functional = firstSubjectBold(subject);
    if (!functional?.bold || !fs.existsSync(functional.bold)) {
      throw new Error(`Recovery smoke subject ${subject.subject_id} has no readable BOLD input.`);
    }
    const derivativeInputDir = path.join(
      project.project_dir,
      "derivatives",
      "rsfmri_preproc",
      subject.subject_id,
      "func"
    );
    fs.mkdirSync(derivativeInputDir, { recursive: true });
    const derivativeInput = path.join(
      derivativeInputDir,
      `filt_smoke_${subject.subject_id}_bold.nii.gz`
    );
    fs.copyFileSync(functional.bold, derivativeInput);
    functional.bold = derivativeInput;
    functional.exists = true;
  }
  const derivativeAtlasDir = path.join(project.project_dir, "derivatives", "atlases");
  fs.mkdirSync(derivativeAtlasDir, { recursive: true });
  const executionAtlasPath = path.join(
    derivativeAtlasDir,
    "agent-first-smoke-atlas.nii.gz"
  );
  fs.copyFileSync(atlasPath, executionAtlasPath);
  const failedSubject = subjects[2];
  const failedFunctional = firstSubjectBold(failedSubject);
  if (!failedFunctional?.bold) {
    throw new Error("Recovery smoke subject does not contain a registered BOLD input.");
  }
  const originalBoldPath = failedFunctional.bold;
  failedFunctional.bold = path.join(
    path.dirname(originalBoldPath),
    `${failedSubject.subject_id}_recoverable_missing_bold.nii.gz`
  );
  writeJsonAtomically(project.dataset_index_path, datasetIndex);

  const created = await requestBackendJson(
    "POST",
    `/api/projects/${encodeURIComponent(project.project_id)}/agent-lifecycles`,
    { command_id: "recovery-smoke:create", actor: "packaged-recovery-smoke" }
  );
  const lifecycleId = created.lifecycle.lifecycle_id;
  const commandPath = `/api/projects/${encodeURIComponent(
    project.project_id
  )}/agent-lifecycles/${encodeURIComponent(lifecycleId)}/commands`;
  await requestBackendJson("POST", commandPath, {
    command_id: "recovery-smoke:prepare:context",
    action: "context_ready",
    actor: "packaged-recovery-smoke",
  });

  const goal = "Compute atlas-grounded functional connectivity for all three registered subjects";
  const plan = {
    pipeline_id: "agent_first_recovery_fc",
    version: "1.0",
    modality: "rs-fMRI",
    description: "Three-subject functional-connectivity recovery acceptance workflow",
    project_context: {
      project_id: project.project_id,
      project_config_path: project.project_config_path,
      project_dir: project.project_dir,
      rawdata_dir: project.rawdata_dir,
      dataset_index_path: project.dataset_index_path,
      source: "created",
      diagnostics: project.diagnostics || {},
    },
    goal,
    execution: { scheduler_mode: "serial", stop_on_failure: false },
    nodes: [
      {
        id: "functional_connectivity_subject",
        backend: "python",
        depends_on: [],
        parallel_level: "subject",
        params: {
          atlas_path: executionAtlasPath,
          backend: "python",
          dataset_index: project.dataset_index_path,
          roi_count: 2,
        },
      },
    ],
    metadata: {
      subject_ids: subjects.map((subject) => subject.subject_id),
      capability_level: "computed",
      recovery_acceptance: true,
    },
  };
  const draft = await requestBackendJson(
    "POST",
    `/api/projects/${encodeURIComponent(project.project_id)}/plans`,
    {
      plan,
      project_config_path: project.project_config_path,
      validation: { ok: true },
      goal,
      provider: "packaged-recovery-smoke",
      reviewed_actor: null,
      lifecycle_id: lifecycleId,
    }
  );
  const candidate = draft.reviewed_plan?.payload?.goal_contract_candidate;
  if (!candidate) {
    throw new Error("Recovery smoke plan did not produce a reviewable Goal Contract candidate.");
  }
  const saved = await requestBackendJson(
    "POST",
    `/api/projects/${encodeURIComponent(project.project_id)}/plans`,
    {
      plan,
      project_config_path: project.project_config_path,
      validation: { ok: true },
      goal,
      provider: "packaged-recovery-smoke",
      goal_contract_candidate: candidate,
      reviewed_actor: "packaged-recovery-smoke",
      lifecycle_id: lifecycleId,
    }
  );
  const reviewed = saved.reviewed_plan;
  const goalContract = reviewed?.payload?.goal_contract;
  if (!goalContract?.goal_contract_id || !goalContract?.goal_contract_hash) {
    throw new Error("Recovery smoke reviewed plan lost its Goal Contract binding.");
  }
  const storedDatasetPath = reviewed?.payload?.plan?.nodes?.[0]?.params?.dataset_index;
  if (storedDatasetPath !== project.dataset_index_path) {
    throw new Error(
      `Recovery smoke reviewed plan lost its dataset binding: ${JSON.stringify({
        expected: project.dataset_index_path,
        actual: storedDatasetPath,
      })}`
    );
  }
  const commands = [
    {
      action: "plan_drafted",
      reviewed_plan_id: reviewed.reviewed_plan_id,
      goal_contract_id: goalContract.goal_contract_id,
      goal_contract_hash: goalContract.goal_contract_hash,
    },
    { action: "plan_validated", reviewed_plan_id: reviewed.reviewed_plan_id },
    { action: "request_approval", reviewed_plan_id: reviewed.reviewed_plan_id },
  ];
  for (let index = 0; index < commands.length; index += 1) {
    await requestBackendJson("POST", commandPath, {
      command_id: `recovery-smoke:prepare:${index + 1}`,
      actor: "packaged-recovery-smoke",
      ...commands[index],
    });
  }
  return {
    lifecycleId,
    failedSubjectId: failedSubject.subject_id,
    datasetIndexPath: project.dataset_index_path,
    originalBoldPath,
    planHash: reviewed.plan_hash,
    reviewedPlanId: reviewed.reviewed_plan_id,
  };
}

function restoreRecoverySmokeInput(project) {
  const fixture = project.recoveryFixture;
  const datasetIndex = JSON.parse(fs.readFileSync(fixture.datasetIndexPath, "utf8"));
  const subject = (datasetIndex.subjects || []).find(
    (item) => item.subject_id === fixture.failedSubjectId
  );
  const functional = firstSubjectBold(subject);
  if (!functional) {
    throw new Error("Recovery smoke could not restore the failed subject input binding.");
  }
  functional.bold = fixture.originalBoldPath;
  writeJsonAtomically(fixture.datasetIndexPath, datasetIndex);
}

async function ensureSmokeProjectFixture() {
  const rawdataDir = process.env.MEDIMAGE_DESKTOP_SMOKE_RAWDATA;
  const projectDir = process.env.MEDIMAGE_DESKTOP_SMOKE_PROJECT_DIR;
  if (!IS_SMOKE_TEST || !rawdataDir || !projectDir) {
    return null;
  }
  const workflow = process.env.MEDIMAGE_DESKTOP_SMOKE_WORKFLOW || "shell";
  const project = await requestBackendJson("POST", "/api/projects/create", {
    project_name: "Packaged Agent-first Smoke",
    rawdata_dir: rawdataDir,
    project_dir: projectDir,
    copy_mode: "reference",
    run_inspection: workflow !== "shell",
    overwrite: false,
  });
  const atlasSource = process.env.MEDIMAGE_DESKTOP_SMOKE_ATLAS_SOURCE;
  const templateSource = process.env.MEDIMAGE_DESKTOP_SMOKE_TEMPLATE_SOURCE;
  if (workflow === "shell" || !atlasSource || !templateSource) {
    return project;
  }
  const resourceDir = path.join(projectDir, "resources", "atlases");
  const atlasPath = path.join(resourceDir, "agent-first-smoke-atlas.nii.gz");
  fs.mkdirSync(resourceDir, { recursive: true });
  fs.copyFileSync(atlasSource, atlasPath);
  const templateDir = path.join(projectDir, "resources", "templates");
  const templatePath = path.join(templateDir, "agent-first-smoke-template.nii.gz");
  fs.mkdirSync(templateDir, { recursive: true });
  fs.copyFileSync(templateSource, templatePath);
  await requestBackendJson(
    "PUT",
    `/api/projects/${encodeURIComponent(project.project_id)}/agent-settings`,
    {
      default_atlas: {
        name: "Agent-first synthetic two-region atlas",
        path: atlasPath,
        license: "CC0-1.0",
      },
      default_template: {
        name: "Agent-first synthetic 3D template",
        path: templatePath,
        license: "CC0-1.0",
      },
      cpu_policy: "auto",
      compute_policy: "auto",
    }
  );
  const configuredProject = { ...project, smokeWorkflow: workflow, atlasPath, templatePath };
  if (workflow === "recovery") {
    return {
      ...configuredProject,
      recoveryFixture: await prepareRecoverySmokeFixture(configuredProject, atlasPath),
    };
  }
  return configuredProject;
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
  let lastSnapshot = null;
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
    lastSnapshot = snapshot;
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
  throw new Error(
    `Frontend renderer did not mount a non-empty React application shell: ${JSON.stringify(lastSnapshot)}`
  );
}

async function verifyAgentFirstNavigation(win, attempts = 60) {
  for (let index = 0; index < attempts; index += 1) {
    const ready = await win.webContents.executeJavaScript(`(() => {
      const projectRow = document.querySelector('article[role="listitem"] button');
      const buttons = Array.from(document.querySelectorAll('nav button'));
      return { hasProjectRow: Boolean(projectRow), navigationCount: buttons.length };
    })()`, true);
    if (ready.hasProjectRow && ready.navigationCount === 4) {
      break;
    }
    await delay(250);
    if (index === attempts - 1) {
      throw new Error("Agent-first navigation fixture did not become available.");
    }
  }

  await win.webContents.executeJavaScript(`(() => {
    document.querySelector('article[role="listitem"] button')?.click();
  })()`, true);
  await delay(500);

  const visited = [];
  for (const targetIndex of [0, 1, 2, 3]) {
    const step = await win.webContents.executeJavaScript(`(() => {
      const buttons = Array.from(document.querySelectorAll('nav button'));
      const target = buttons[${targetIndex}];
      if (!target || target.disabled) {
        return { clicked: false };
      }
      target.click();
      return { clicked: true, label: target.getAttribute('aria-label') || '' };
    })()`, true);
    if (!step.clicked) {
      throw new Error(`Agent-first navigation item ${targetIndex} is unavailable after project selection.`);
    }
    await delay(350);
    const selected = await win.webContents.executeJavaScript(`(() => {
      const buttons = Array.from(document.querySelectorAll('nav button'));
      return buttons.findIndex((button) => button.getAttribute('aria-current') === 'page');
    })()`, true);
    visited.push({ index: targetIndex, label: step.label, selectedIndex: selected });
  }

  const snapshot = await win.webContents.executeJavaScript(`(() => {
    const buttons = Array.from(document.querySelectorAll('nav button'));
    return {
      labels: buttons.map((button) => button.getAttribute('aria-label') || ''),
      navigationCount: buttons.length,
      disabledCount: buttons.filter((button) => button.disabled).length,
    };
  })()`, true);
  return { ...snapshot, visited };
}

function readJsonEvidence(filePath) {
  if (!filePath || !fs.existsSync(filePath)) {
    return null;
  }
  try {
    return JSON.parse(fs.readFileSync(filePath, "utf8"));
  } catch (error) {
    return { readError: String(error) };
  }
}

async function collectWorkflowRunEvidence(project, task) {
  const runId = task?.technical_details?.run_id;
  if (!runId) {
    return null;
  }
  const run = await requestBackendJson(
    "GET",
    `/api/projects/${encodeURIComponent(project.project_id)}/runs/${encodeURIComponent(runId)}`
  );
  const executorResult = run?.run_link?.payload?.executor_result || {};
  const nodeStates = (executorResult.node_states || []).map((statePath) => ({
    statePath,
    state: readJsonEvidence(statePath),
  }));
  const summaryPath = executorResult.summary_path || null;
  const summary = readJsonEvidence(summaryPath);
  const nativeManifestPath = summaryPath
    ? path.resolve(
        path.dirname(summaryPath),
        "..",
        "..",
        "..",
        "preprocessing_native_runs",
        runId,
        "native_full_run_manifest.json"
      )
    : null;
  const nativeManifest = readJsonEvidence(nativeManifestPath);
  const resourceProvenance = readJsonEvidence(nativeManifest?.resource_provenance_path);
  return {
    run: run?.run_link || null,
    summaryPath,
    summary,
    nodeStates,
    nativeManifestPath,
    nativeManifest,
    resourceProvenance,
  };
}

function collectExistingOutputPaths(value, paths = new Set()) {
  if (typeof value === "string") {
    if (fs.existsSync(value) && fs.statSync(value).isFile()) {
      paths.add(path.resolve(value));
    }
    return paths;
  }
  if (Array.isArray(value)) {
    for (const item of value) collectExistingOutputPaths(item, paths);
    return paths;
  }
  if (value && typeof value === "object") {
    for (const item of Object.values(value)) collectExistingOutputPaths(item, paths);
  }
  return paths;
}

function snapshotUntouchedSubjectOutputs(runEvidence, subjects) {
  const paths = new Set();
  for (const item of runEvidence?.nodeStates || []) {
    if (!subjects.includes(item.state?.subject)) continue;
    collectExistingOutputPaths(item.state?.outputs, paths);
  }
  const snapshot = Array.from(paths)
    .sort()
    .map((filePath) => {
      const stat = fs.statSync(filePath);
      return {
        path: filePath,
        size: stat.size,
        mtimeMs: stat.mtimeMs,
        sha256: crypto.createHash("sha256").update(fs.readFileSync(filePath)).digest("hex"),
      };
    });
  if (snapshot.length === 0) {
    throw new Error("Recovery smoke found no successful untouched-subject outputs to preserve.");
  }
  return snapshot;
}

async function verifyBidsToFcWorkflow(win, project, attempts = 360) {
  await win.webContents.executeJavaScript(`(() => {
    if (window.__medimageSmokeFetchInstalled) return;
    window.__medimageSmokeFetchInstalled = true;
    window.__medimageSmokeFetchErrors = [];
    const originalFetch = window.fetch.bind(window);
    window.fetch = async (...args) => {
      const response = await originalFetch(...args);
      if (!response.ok) {
        try {
          window.__medimageSmokeFetchErrors.push({
            body: await response.clone().text(),
            status: response.status,
            url: String(args[0]),
          });
        } catch {
          // Preserve the application response even when diagnostics cannot clone it.
        }
      }
      return response;
    };
  })()`, true);
  const navigation = await navigateToAgentWorkspace(win);
  if (!navigation) {
    throw new Error("The Agent navigation item was unavailable for the BIDS workflow.");
  }

  let goalSubmitted = false;
  const submittedDecisionBatches = new Set();
  const maximumDecisionSubmissions = project.smokeWorkflow === "dicom" ? 2 : 1;
  let approvalSubmitted = false;
  let approvalRenderMisses = 0;
  let explicitOperations = 0;
  let lastTask = null;
  const goalText =
    project.smokeWorkflow === "dicom"
      ? "Convert registered DICOM to NIfTI, run rs-fMRI preprocessing, and generate FC"
      : "生成 FC";
  const maximumAttempts = project.smokeWorkflow === "dicom" ? Math.max(attempts, 960) : attempts;
  for (let index = 0; index < maximumAttempts; index += 1) {
    if (!goalSubmitted) {
      goalSubmitted = await win.webContents.executeJavaScript(`(() => {
        const textarea = document.querySelector('main textarea');
        const form = textarea?.closest('form');
        if (!textarea || !form) return false;
        const setter = Object.getOwnPropertyDescriptor(
          window.HTMLTextAreaElement.prototype,
          'value'
        )?.set;
        setter?.call(
          textarea,
          ${JSON.stringify(goalText)}
        );
        textarea.dispatchEvent(new Event('input', { bubbles: true }));
        form.requestSubmit();
        return true;
      })()`, true);
      if (goalSubmitted) explicitOperations += 1;
    }

    const page = await requestBackendJson(
      "GET",
      `/api/projects/${encodeURIComponent(project.project_id)}/agent/tasks`
    );
    lastTask = Array.isArray(page.items) ? page.items[0] || null : null;
    const action = lastTask?.next_action?.type || "";
    if (approvalSubmitted && action === "approve_execution") {
      const fetchErrors = await win.webContents.executeJavaScript(
        `window.__medimageSmokeFetchErrors || []`,
        true
      );
      if (fetchErrors.length) {
        throw new Error(`Approval request failed: ${JSON.stringify(fetchErrors)}`);
      }
    }

    const decisionBatchId = lastTask?.decision_batch?.batch_id || "";
    if (
      action === "answer_science_decision" &&
      decisionBatchId &&
      !submittedDecisionBatches.has(decisionBatchId) &&
      submittedDecisionBatches.size < maximumDecisionSubmissions
    ) {
      const submitted = await win.webContents.executeJavaScript(`(async () => {
        const dialog = document.querySelector('[role="dialog"]');
        const form = dialog?.querySelector('form');
        if (!form) return false;
        const options = Array.from(form.querySelectorAll('input[type="radio"]'));
        const optionGroups = new Map();
        for (const option of options) {
          const group = optionGroups.get(option.name) || [];
          group.push(option);
          optionGroups.set(option.name, group);
        }
        for (const group of optionGroups.values()) {
          if (!group.some((option) => option.checked)) {
            group[0]?.click();
            await new Promise((resolve) => setTimeout(resolve, 50));
          }
        }
        form.requestSubmit();
        return true;
      })()`, true);
      if (submitted) {
        submittedDecisionBatches.add(decisionBatchId);
        explicitOperations += 1;
      }
    }

    if (!approvalSubmitted && action === "approve_execution") {
      const approvalAttempt = await win.webContents.executeJavaScript(`(async () => {
        const candidates = Array.from(document.querySelectorAll(
          '[role="dialog"] [data-agent-action="approve_execution"]'
        ));
        const button = candidates.find((item) => !item.disabled);
        if (!button) {
          const reopen = Array.from(document.querySelectorAll(
            'main [data-agent-action="reopen_approve_execution"]'
          )).find((item) => !item.disabled);
          if (reopen) {
            reopen.click();
          } else {
            const navigation = Array.from(document.querySelectorAll('nav button'));
            navigation[0]?.click();
            await new Promise((resolve) => setTimeout(resolve, 100));
            navigation[1]?.click();
          }
          return {
            submitted: false,
            dialogCount: document.querySelectorAll('[role="dialog"]').length,
            buttons: Array.from(document.querySelectorAll('button')).map((item) => ({
              text: item.textContent?.trim() || '',
              disabled: item.disabled,
              primary: item.getAttribute('data-primary-action'),
            })),
          };
        }
        button.click();
        return { submitted: true };
      })()`, true);
      if (approvalAttempt.submitted) {
        approvalSubmitted = true;
        explicitOperations += 1;
      } else {
        approvalRenderMisses += 1;
        if (approvalRenderMisses >= 12) {
          throw new Error(
            `Approval action did not render after remount: ${JSON.stringify(approvalAttempt)}`
          );
        }
      }
    }

    const truthfulPartialHandoff =
      lastTask?.state === "needs_attention" &&
      lastTask?.outcome === "partial" &&
      lastTask?.technical_details?.internal_state === "HUMAN_HANDOFF" &&
      Boolean(lastTask?.technical_details?.evaluation_id) &&
      Array.isArray(lastTask?.result_summary?.artifacts) &&
      lastTask.result_summary.artifacts.some(
        (artifact) => artifact.artifact_type === "fc_matrix" && artifact.reload_status === "passed"
      );
    if (lastTask?.state === "completed" || truthfulPartialHandoff) {
      const resultVisible = await win.webContents.executeJavaScript(`(() => {
        const main = document.querySelector('main');
        return Boolean(main && main.textContent && main.textContent.length > 0);
      })()`, true);
      if (!resultVisible) {
        throw new Error("The completed BIDS workflow did not render a result surface.");
      }
      const maximumOperations = project.smokeWorkflow === "dicom" ? 4 : 3;
      if (explicitOperations > maximumOperations) {
        throw new Error(`The ${project.smokeWorkflow} workflow required ${explicitOperations} explicit operations.`);
      }
      const runEvidence = await collectWorkflowRunEvidence(project, lastTask);
      return {
        explicitOperations,
        decisionSubmissions: submittedDecisionBatches.size,
        decisionSubmitted: submittedDecisionBatches.size > 0,
        approvalSubmitted,
        task: lastTask,
        resultVisible,
        truthfulPartialHandoff,
        runEvidence,
      };
    }
    if (
      lastTask?.next_action?.requires_user &&
      !["answer_science_decision", "approve_execution"].includes(action)
    ) {
      const encodedProjectId = encodeURIComponent(project.project_id);
      const encodedTaskId = encodeURIComponent(lastTask.task_id);
      const encodedRunId = lastTask.technical_details?.run_id
        ? encodeURIComponent(lastTask.technical_details.run_id)
        : null;
      const [run, runTimeline, runLogs] = await Promise.all([
        encodedRunId
          ? requestBackendJson("GET", `/api/projects/${encodedProjectId}/runs/${encodedRunId}`)
          : null,
        encodedRunId
          ? requestBackendJson(
              "GET",
              `/api/projects/${encodedProjectId}/runs/${encodedRunId}/state-timeline`
            )
          : null,
        encodedRunId
          ? requestBackendJson(
              "GET",
              `/api/projects/${encodedProjectId}/runs/${encodedRunId}/logs?max_bytes=200000`
            )
          : null,
      ]);
      const executorNodeStatePaths = run?.run_link?.payload?.executor_result?.node_states || [];
      const executorNodeStates = executorNodeStatePaths.map((statePath) => {
        try {
          return JSON.parse(fs.readFileSync(statePath, "utf8"));
        } catch (error) {
          return { path: statePath, readError: String(error) };
        }
      });
      const executorSummaryPath = run?.run_link?.payload?.executor_result?.summary_path;
      let executorSummary = null;
      let nativeManifest = null;
      if (executorSummaryPath) {
        try {
          executorSummary = JSON.parse(fs.readFileSync(executorSummaryPath, "utf8"));
          const nativeManifestPath = path.resolve(
            path.dirname(executorSummaryPath),
            "..",
            "..",
            "..",
            "preprocessing_native_runs",
            lastTask.technical_details.run_id,
            "native_full_run_manifest.json"
          );
          if (fs.existsSync(nativeManifestPath)) {
            nativeManifest = JSON.parse(fs.readFileSync(nativeManifestPath, "utf8"));
            nativeManifest.subject_stage_issues = (nativeManifest.subject_execution || []).map(
              (subject) => {
                try {
                  const manifest = JSON.parse(fs.readFileSync(subject.manifest_path, "utf8"));
                  return {
                    subject_id: subject.subject_id,
                    stages: (manifest.stage_results || [])
                      .filter((stage) => ["blocked", "failed"].includes(stage.status))
                      .map((stage) => ({
                        stage_id: stage.stage_id,
                        status: stage.status,
                        errors: stage.errors,
                        blocking_issues: stage.blocking_issues,
                      })),
                  };
                } catch (error) {
                  return { subject_id: subject.subject_id, readError: String(error) };
                }
              }
            );
          }
        } catch (error) {
          executorSummary = { path: executorSummaryPath, readError: String(error) };
        }
      }
      throw new Error(
        `${project.smokeWorkflow} workflow stopped for unexpected attention: ${JSON.stringify({
          task: {
            state: lastTask.state,
            outcome: lastTask.outcome,
            goalSummary: lastTask.goal_summary,
            currentActionCode: lastTask.current_action_code,
            technicalDetails: lastTask.technical_details,
            resultSummary: lastTask.result_summary,
            nextAction: lastTask.next_action,
            decisionBatch: lastTask.decision_batch,
          },
          project,
          run: run?.run_link
            ? {
                status: run.run_link.status,
                executorResult: {
                  status: run.run_link.payload?.executor_result?.status,
                  errors: run.run_link.payload?.executor_result?.errors,
                  nodeStates: executorNodeStates,
                  summary: executorSummary,
                  nativeManifest: nativeManifest
                    ? {
                        ok: nativeManifest.ok,
                        status: nativeManifest.status,
                        artifactCount: nativeManifest.artifact_count,
                        subjectExecution: nativeManifest.subject_execution,
                        errors: nativeManifest.errors,
                        blockingIssues: nativeManifest.blocking_issues,
                        subjectStageIssues: nativeManifest.subject_stage_issues,
                      }
                    : null,
                },
              }
            : null,
          timeline: runTimeline
            ? {
                nodes: runTimeline.nodes,
                errors: runTimeline.errors,
                warnings: runTimeline.warnings,
              }
            : null,
          logs: runLogs,
        })}`
      );
    }
    await delay(500);
  }
  throw new Error(`${project.smokeWorkflow} workflow did not complete: ${JSON.stringify(lastTask)}`);
}

async function navigateToAgentWorkspace(win, attempts = 50) {
  for (let index = 0; index < attempts; index += 1) {
    const navigation = await win.webContents.executeJavaScript(`(() => {
      const target = Array.from(document.querySelectorAll('nav button'))[1];
      if (!target || target.disabled) return false;
      target.click();
      return true;
    })()`, true);
    if (navigation) return true;
    await delay(200);
  }
  return false;
}

async function verifyRecoveryWorkflow(win, project, attempts = 720) {
  const navigation = await navigateToAgentWorkspace(win);
  if (!navigation) {
    throw new Error("The Agent navigation item was unavailable for the recovery workflow.");
  }

  let initialApprovalSubmitted = false;
  let recoveryApprovalSubmitted = false;
  let recoveryInputRestored = false;
  let proposalTask = null;
  let lastTask = null;
  let parentRunEvidence = null;
  let untouchedArtifactsBefore = null;
  for (let index = 0; index < attempts; index += 1) {
    const page = await requestBackendJson(
      "GET",
      `/api/projects/${encodeURIComponent(project.project_id)}/agent/tasks`
    );
    lastTask = (page.items || []).find(
      (item) => item.task_id === project.recoveryFixture.lifecycleId
    ) || null;
    const action = lastTask?.next_action?.type || "";

    if (
      recoveryApprovalSubmitted &&
      ["completed", "needs_attention"].includes(lastTask?.state) &&
      lastTask?.technical_details?.evaluation_id &&
      lastTask.technical_details.evaluation_id !==
        proposalTask?.technical_details?.evaluation_id
    ) {
      const lifecyclePath = `/api/projects/${encodeURIComponent(
        project.project_id
      )}/agent-lifecycles/${encodeURIComponent(lastTask.task_id)}`;
      const attemptsPage = await requestBackendJson(
        "GET",
        `${lifecyclePath}/recovery-attempts`
      );
      const recoveryAttempts = attemptsPage.recovery_attempts || [];
      const latestRecoveryAttempt = recoveryAttempts.find(
        (attempt) =>
          attempt.goal_evaluation_id === lastTask.technical_details.evaluation_id
      ) || recoveryAttempts[0] || null;
      if (
        !latestRecoveryAttempt ||
        latestRecoveryAttempt.status !== "EVALUATED" ||
        !["SUCCESS", "COMPLETED"].includes(
          String(latestRecoveryAttempt.execution_status || "").toUpperCase()
        )
      ) {
        throw new Error(
          `Recovery attempt was not successfully evaluated: ${JSON.stringify({
            task: lastTask,
            recoveryAttempts,
          })}`
        );
      }
      const events = await requestBackendJson(
        "GET",
        `/api/projects/${encodeURIComponent(project.project_id)}/agent/tasks/${encodeURIComponent(
          lastTask.task_id
        )}/events?limit=100`
      );
      const untouchedArtifactsAfter = snapshotUntouchedSubjectOutputs(parentRunEvidence, [
        "sub-001",
        "sub-002",
      ]);
      if (JSON.stringify(untouchedArtifactsBefore) !== JSON.stringify(untouchedArtifactsAfter)) {
        throw new Error("Recovery smoke modified outputs belonging to untouched subjects.");
      }
      return {
        initialApprovalSubmitted,
        recoveryApprovalSubmitted,
        recoveryInputRestored,
        proposalTask,
        task: lastTask,
        events: events.items || [],
        recoveryAttempts,
        latestRecoveryAttempt,
        truthfulPartialHandoff: lastTask.state === "needs_attention",
        explicitOperations: 1,
        parentRunEvidence,
        untouchedArtifactsBefore,
        untouchedArtifactsAfter,
      };
    }

    if (action === "view_attention" && lastTask?.recovery?.proposal_id) {
      const lifecyclePath = `/api/projects/${encodeURIComponent(
        project.project_id
      )}/agent-lifecycles/${encodeURIComponent(lastTask.task_id)}`;
      const proposal = await requestBackendJson(
        "GET",
        `${lifecyclePath}/recovery-proposals/${encodeURIComponent(
          lastTask.recovery.proposal_id
        )}`
      );
      const diagnosisId = proposal.recovery_proposal?.diagnosis_id ||
        lastTask.technical_details?.diagnosis_id;
      const diagnosis = diagnosisId
        ? await requestBackendJson(
            "GET",
            `${lifecyclePath}/recovery-diagnoses/${encodeURIComponent(diagnosisId)}`
          )
        : null;
      const attemptsPage = await requestBackendJson(
        "GET",
        `${lifecyclePath}/recovery-attempts`
      );
      const recoveryAttempts = attemptsPage.recovery_attempts || [];
      const recoveryRuns = [];
      for (const attempt of recoveryAttempts) {
        if (!attempt.recovery_run_id) continue;
        const encodedRunId = encodeURIComponent(attempt.recovery_run_id);
        const run = await requestBackendJson(
          "GET",
          `/api/projects/${encodeURIComponent(project.project_id)}/runs/${encodedRunId}`
        );
        const summaryPath = run?.run_link?.summary_path || null;
        const stateRoot = run?.run_link?.payload?.state_root || null;
        const statesPath = stateRoot
          ? path.join(stateRoot, "states", attempt.recovery_run_id)
          : null;
        const nodeStates = [];
        if (statesPath && fs.existsSync(statesPath)) {
          for (const entry of fs.readdirSync(statesPath, { recursive: true, withFileTypes: true })) {
            if (!entry.isFile() || !entry.name.endsWith(".json")) continue;
            nodeStates.push(readJsonEvidence(path.join(entry.parentPath, entry.name)));
          }
        }
        recoveryRuns.push({
          run,
          summaryPath,
          summary: readJsonEvidence(summaryPath),
          nodeStates,
          graph: await requestBackendJson(
            "GET",
            `/api/projects/${encodeURIComponent(project.project_id)}/runs/${encodedRunId}/graph`
          ),
        });
      }
      throw new Error(
        `Recovery proposal was not executable: ${JSON.stringify({
          task: lastTask,
          proposal,
          diagnosis,
          recoveryAttempts,
          recoveryRuns,
        })}`
      );
    }

    if (!initialApprovalSubmitted && action === "approve_execution") {
      const submitted = await win.webContents.executeJavaScript(`(async () => {
        let button = Array.from(document.querySelectorAll(
          '[role="dialog"] [data-agent-action="approve_execution"]'
        )).find((item) => !item.disabled);
        if (!button) {
          Array.from(document.querySelectorAll(
            'main [data-agent-action="reopen_approve_execution"]'
          )).find((item) => !item.disabled)?.click();
          await new Promise((resolve) => setTimeout(resolve, 100));
          button = Array.from(document.querySelectorAll(
            '[role="dialog"] [data-agent-action="approve_execution"]'
          )).find((item) => !item.disabled);
        }
        if (!button) return false;
        button.click();
        return true;
      })()`, true);
      if (submitted) {
        initialApprovalSubmitted = true;
      } else if (index >= 20) {
        throw new Error("Initial recovery execution approval was not rendered.");
      }
    }

    if (action === "approve_recovery" && lastTask?.recovery) {
      proposalTask = lastTask;
      const affected = lastTask.recovery.affected_subjects || [];
      const untouched = lastTask.recovery.untouched_scope || [];
      if (
        affected.length !== 1 ||
        affected[0] !== project.recoveryFixture.failedSubjectId ||
        !untouched.includes("sub-001") ||
        !untouched.includes("sub-002")
      ) {
        throw new Error(`Recovery proposal scope was not isolated: ${JSON.stringify(lastTask.recovery)}`);
      }
      if (!recoveryInputRestored) {
        parentRunEvidence = await collectWorkflowRunEvidence(project, lastTask);
        untouchedArtifactsBefore = snapshotUntouchedSubjectOutputs(parentRunEvidence, [
          "sub-001",
          "sub-002",
        ]);
        restoreRecoverySmokeInput(project);
        recoveryInputRestored = true;
      }
      if (!recoveryApprovalSubmitted) {
        const submitted = await win.webContents.executeJavaScript(`(async () => {
          let button = Array.from(document.querySelectorAll(
            '[role="dialog"] [data-agent-action="approve_recovery"]'
          )).find((item) => !item.disabled);
          if (!button) {
            Array.from(document.querySelectorAll(
              'main [data-agent-action="reopen_approve_recovery"]'
            )).find((item) => !item.disabled)?.click();
            await new Promise((resolve) => setTimeout(resolve, 100));
            button = Array.from(document.querySelectorAll(
              '[role="dialog"] [data-agent-action="approve_recovery"]'
            )).find((item) => !item.disabled);
          }
          if (!button) return false;
          button.click();
          return true;
        })()`, true);
        if (submitted) {
          recoveryApprovalSubmitted = true;
        } else if (index >= 20) {
          const rendererSnapshot = await win.webContents.executeJavaScript(`(() => ({
            mainText: document.querySelector('main')?.textContent?.slice(0, 4000) || '',
            buttons: Array.from(document.querySelectorAll('button')).map((button) => ({
              action: button.getAttribute('data-agent-action'),
              disabled: button.disabled,
              text: button.textContent,
            })),
            dialogs: Array.from(document.querySelectorAll('[role="dialog"]')).map(
              (dialog) => dialog.textContent?.slice(0, 2000) || ''
            ),
          }))()`, true);
          throw new Error(
            `Recovery approval action was not rendered: ${JSON.stringify({
              rendererSnapshot,
              task: lastTask,
            })}`
          );
        }
      }
    }

    await delay(500);
  }
  throw new Error(`Recovery workflow did not complete: ${JSON.stringify(lastTask)}`);
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
    show: !IS_SMOKE_TEST || IS_VISIBLE_SMOKE_TEST,
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

  const smokeProject = await ensureSmokeProjectFixture();
  await loadFrontend(win);

  if (IS_SMOKE_TEST) {
    const renderer = await verifyFrontendRenderer(win);
    const agentFirstNavigation = smokeProject
      ? await verifyAgentFirstNavigation(win)
      : null;
    const bidsToFc =
      ["bids", "dicom"].includes(smokeProject?.smokeWorkflow)
        ? await verifyBidsToFcWorkflow(win, smokeProject)
        : null;
    const recovery =
      smokeProject?.smokeWorkflow === "recovery"
        ? await verifyRecoveryWorkflow(win, smokeProject)
        : null;
    if (rendererExit) {
      throw new Error(`Frontend renderer exited during smoke verification: ${rendererExit.reason}`);
    }
    if (rendererConsoleErrors.length > 0) {
      throw new Error(
        `Frontend renderer emitted ${rendererConsoleErrors.length} console error(s): ${rendererConsoleErrors[0].message}`
      );
    }
    const finalScreenshot = await captureSmokeScreenshot(win);
    writeSmokeResult({
      frontendIndex: resolveFrontendIndex(),
      frontendLoaded: true,
      rendererVerified: true,
      renderer,
      agentFirstNavigation,
      bidsToFc,
      recovery,
      smokeProject,
      rendererConsoleErrors,
      finalScreenshot,
    });
    app.quit();
  }

  return win;
}

// Packaged smoke runs use a fresh, isolated userData root and may overlap with
// a developer-owned desktop instance. They must not steal or depend on the
// production singleton lock; normal application launches remain single-owner.
const hasSingleInstanceLock = IS_SMOKE_TEST || app.requestSingleInstanceLock();

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
        writeSmokeResult({
          frontendLoaded: false,
          rendererVerified: false,
          error: error.message,
          errorStack: error.stack || "",
        });
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

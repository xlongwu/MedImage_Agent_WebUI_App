# Project State

Current as of 2026-08-09.

## Version and Branch

- Current source/release line: `v0.6.0-rc1`.
- Release convergence target: `v0.6.0-rc2`. This is a stabilization release;
  `v0.7.0-rc1` is reserved for a separately approved capability or contract
  expansion.
- Backend `APP_VERSION` (`src/backend/app/version.py`) is `0.6.0-rc1`.
  All package surface versions (frontend, desktop/electron, pyproject.toml)
  aligned to `0.6.0-rc1` as of 2026-06-20 architecture audit.
- Release-state baseline branch: `main`. The current working tree contains the
  Phase 10 Agent-first source implementation, but it has no clean exact-SHA,
  packaging, tag, or release claim yet.
- Local Git tags present:
  - `v0.3.0-rc1` tagged 2026-06-06
  - `v0.4.0-rc1` tagged 2026-06-10
  - `v0.5.0-rc1` tagged 2026-06-11

Historical release notes live under `docs/发布记录/` and should remain tied to
their tag state.

## Implemented Capabilities

- Deterministic Plan-then-Execute architecture.
- FastAPI backend with domain routers, structured errors, request IDs,
  structured logging, API v1 compatibility rewrite, rate limiting, and CORS.
- `ConfigService` backed configuration and legacy `get_backend_settings()`.
- Project store dependency protocol for read-side routes.
- Atomic runtime state JSON writes with `_schema_version`.
- Plugin-based node registry with duplicate-ID checks and a canonical
  `NodeContract` for every registered runner plus explicitly declared
  planning-only groups. Startup consistency checks reject missing, duplicate,
  incomplete, or conflicting contracts. Tool Catalog is a read-only
  presentation projection and has no ID-prefix safety fallback.
- Approval Gate for file-writing or execution actions.
- Real-project creation from BIDS/rawdata directories, project config and
  dataset index generation, reviewed plan persistence, run links, run history,
  run summaries, and artifact previews.
- DICOM/FunRaw/T1Raw detection, DICOM conversion dry-run, conversion review
  packages, release readiness, release approval metadata, rollback support, and
  a default-blocked native conversion handoff inside the sole reviewed execution
  gateway.
- The in-project Python DICOM converter supports classic single-frame MR and
  Siemens single-frame mosaic MR. All three DemoData subjects were converted on
  Windows into six reloadable NIfTI/JSON pairs with the 1,104-file rawdata
  snapshot unchanged. The reviewed native handoff can reuse this verified
  conversion registry without rerunning conversion.
- Feature-flagged frontend execute UI for DICOM conversion; hidden by default.
- Reviewed rs-fMRI preprocessing workflow for converted inputs, including a
  unified stage catalog, artifact registry and lineage, Minimal FC backend
  chain, optional DPARSFA-like stage semantics, reviewed orchestrator endpoint,
  stage output registration, validation, report export, and a frontend
  reviewed flow that can create a preprocessing run from registered converted
  input, submit the reviewed gate, show stage status, and expose FC artifact
  metadata handoff links.
- Native full preprocessing supports a conservative subject scheduler: serial
  remains the default, while reviewed `process`/`auto` policies bound worker and
  thread counts by the request, CPU capacity, and available-memory estimates.
  Async native runs persist per-subject progress, heartbeat, and terminal state;
  missing memory telemetry fails back to serial scheduling.
- Frontend API wrappers under `src/frontend/src/lib/api/` with a shared client.
  Idempotent GET requests receive one bounded retry after a transport failure;
  mutating requests are never retried automatically.
- The default local Assistant is a deterministic, read-only project-state
  summarizer. It reads the injected `ProjectStore`, reports dataset, plan,
  preprocessing-setup, and execution-run evidence, and never treats chat text
  as approval or execution authority. A real LLM provider remains disabled
  until explicitly configured.
- A server-issued Execution Ticket v3 and single Execution Gateway bind the
  Reviewed Plan, Approval Summary, memory snapshot, project identity, goal and
  evaluation, allowlists, paths, audit context, expiry, and retry policy before
  Pipeline Runtime dispatch. Immutable dispatch records and ordered events
  bind tickets to runs. Same-command replay returns the persisted result;
  prepared work may resume before start, while started-without-outcome is
  reported as unknown and is never automatically repeated.
- Runner dispatch enforces node, backend, input-root, output-root, rawdata,
  allowlist-fingerprint, and ticket constraints before invoking registered
  execution code.
- A persistent Agent lifecycle now separates execution status, observations,
  goal evaluation, recovery proposals, approvals, attempts, and human handoff.
- Observation, deterministic Goal Evaluation, side-effect-free recovery
  proposals, and controlled retry/resume/local-replan services are implemented
  and covered by source-level regression tests.
- The optional controlled single-Agent Harness is default-disabled and bounded
  to six schema-validated advisory actions. It persists redacted context and
  ordered steps, processes one leased step at a time, and has no approval,
  ticket, gateway, runner, shell, or file/database-write capability. A
  lifespan-owned scheduler now advances up to three persisted safe steps per
  wake, yields fairly, recovers only ready/expired attempts on startup, and is
  never invoked by read APIs. Provider-unavailable failures stop the model
  attempt, expose the recorded fallback path, then use a fresh deterministic
  planning hash without reusing an Approval Summary; config/schema/budget
  failures still stop structurally. The 2026-08-09 source/entry/focused-test
  baseline is recorded in `specs/阶段记录/阶段十二/Agent改造/01_当前Agent基线与差距分析.md`;
  no Harness-specific packaged smoke or formal release evidence was located,
  so those two release surfaces remain `unknown` rather than inferred.
- The Phase 10 source tree adds an Agent-first project workspace backed by a
  project-scoped Agent Task read projection. Goal commands stop for unresolved
  science decisions, produce one hashed Approval Summary, and reuse the
  Reviewed Plan, Approval Gate, Execution Ticket, and sole Execution Gateway.
  Approval performs one immediate terminal reconciliation before returning;
  non-terminal runs continue under a bounded single-owner monitor. Read-side
  Agent Task APIs remain side-effect free, and recovery remains a separate
  explicit approval.
- The desktop Runs workspace uses persisted project run links as its authoritative
  source. Exact duplicate run IDs resolve to the newest backend state; Workspace
  keeps only the newest attempt for each Agent Task (or legacy reviewed plan),
  while History retains every distinct attempt. New reviewed executions persist
  their Agent Task lifecycle ID, and terminal result summaries are projected only
  when the selected run has that exact task/run association.
- Runs now joins the exact selected run to its preprocessing artifact registry
  and project-scoped audit projection. Numerical preprocessing, QC, report,
  derivative, state, summary, and pipeline artifacts remain run-scoped; paths
  registered to another preprocessing run are rejected. Run-event and state
  timelines are rendered chronologically, and a run ID is never used as an
  Agent Task ID.
- A complete, reloadable, provenance-bound numerical result remains `computed`
  when its reviewed contract permits a scientifically simplified method. The
  simplification remains an explicit limitation and prevents `validated`; it
  no longer incorrectly converts the capability level to `metadata_only`.
- Native preprocessing report scope is explicit. `group_summary=false` creates
  no group summary, and a completed single-subject run registers exactly one
  validation report and one final report with run/subject scope preserved.
- Registered-BIDS ReHo goals use the native preprocessing orchestrator with
  realignment, motion QC, nuisance regression, detrending, temporal filtering,
  and ReHo enabled. Legacy ReHo plans without a reviewed preprocessed input or
  an upstream realignment/smoothing producer are rejected before dispatch.
- Native DICOM conversion is registered as a reviewed gateway node before
  native preprocessing only when a prepared conversion run has persisted
  release evidence and `agent_conversion_execution_ready=true`; an already
  converted BIDS handoff does not schedule conversion again.
  Partial conversion never marks preprocessing input ready.
- A project-scoped Memory Domain is implemented behind default-closed install,
  generation, use, LLM, and file-projection gates. It uses an independent
  SQLite authority fed by transactional desktop-store outbox records, supports
  deterministic candidate review/consolidation, provenance, FTS retrieval,
  version-bound mutation, tombstone-based forgetting, and a rebuildable
  optional projection. Typed memory snapshots are frozen into Reviewed Plans
  and Approval Summaries. Scientific memories remain advisory and always stop
  for current-task confirmation before they can influence a plan.
- Memory retrieval now has explicit disabled, enabled, and partial typed
  contexts plus an operational health projection. Disabled mode does not probe
  storage. When enabled, failed memory DB health or outbox preflight blocks
  planning with structured errors instead of silently continuing empty;
  recoverable lag/retry/dead-letter/lease/forget conditions remain visible as
  partial warnings. GET projections are side-effect free.
- Planner providers are explicitly `rule_based` or `openai_compatible`; the
  former `mock` provider is removed. Both return the same strict Pydantic plan
  schema and persist redacted invocation/evidence provenance. The remote path
  permits one identical-input JSON repair and never falls back to another plan.
- Automatic AC-PC alignment remains a real computed Python workflow with
  reloadable artifacts and provenance, but is not claimed `validated` without
  an independent manual-reference dataset. The former GUI Agent product routes,
  nodes, schemas and configuration are removed; old route/node IDs are rejected.

## Current Execution Boundaries

- Rawdata is read-only.
- The Pipeline Runtime remains the only pipeline execution path.
- LLM output is advisory only.
- Memory is project-scoped and is neither an execution permission nor a source
  of scientific validity, capability, approval, or current environment truth.
  Disabling generation/use does not delete existing memory; forgetting scrubs
  stored plaintext and prevents old sources from recreating the forgotten
  generation.
- DICOM conversion execution is not automatic. It requires explicit environment
  flags, release approval/readiness evidence, confirmation payloads, audit
  package evidence, checksum/rollback checks, and safe output roots.
- Agent-first visibility and a single approval card do not relax any execution
  gate. Advanced mode affects navigation only.
- Reviewed preprocessing uses in-project Python kernels. MATLAB, SPM, and
  DPABI executables are outside the supported execution path.
- Run artifact discovery accepts managed evidence under project `data/` in
  addition to `work`, `logs`, `reports`, and `derivatives`; rawdata and paths
  outside the project output boundary remain rejected.
- Reviewed Minimal FC can continue from already registered realignment outputs;
  this is a resume/registration path. It is not a one-click local SPM
  realignment execution claim while MATLAB/SPM gates remain unsatisfied.
- Full DICOM-to-reviewed-FC GUI E2E on real multi-subject data, true
  multi-subject workflow validation, group statistics, classification,
  clinical diagnosis, report editing, and auto-update are not current capabilities.
- During the `v0.6.0-rc2` convergence window, `main` is frozen for new execution
  paths, scientific algorithms, capability-level upgrades, public API expansion,
  and dependency expansion. Only release-blocking fixes, tests, evidence, and
  documentation corrections may enter without reopening capability review.

## Validation Baseline

- Required backend interpreter: use the Python interpreter from the active
  project environment (e.g. `.venv/Scripts/python.exe` on Windows). The
  maintainer's local validation environment is recorded in release-specific
  validation evidence, not as a repository requirement. Do NOT hardcode
  maintainer-local interpreter paths in stable documentation.

- Use `--basetemp=.pytest_tmp` when Windows temp directories contain locked
  pytest temp entries.
- Expected optional skips commonly include missing `cupy` and missing
  `MEDIMAGE_EXTERNAL_BIDS_SMOKE_DIR`. `pydicom` is now a core dependency
  because the packaged desktop exposes the reviewed native DICOM workflow.
- The current RC2 working tree was validated on Windows with Python 3.11.15:
  backend `4108 passed, 16 skipped`; frontend format check, typecheck, `238`
  tests, and production build passed. The only backend skip caused by Windows
  privilege rather than an intentionally disabled optional/external path was
  the symlink-escape case.
- Exact packaging candidate `6a392c15079f51c16a8e3c2a035915972aabd9ff`
  completed GitHub Actions run `29469529639` successfully. Its `backend`,
  `frontend`, and `desktop` jobs all passed. This closes the remote-CI evidence
  gate for that source candidate; later runtime-affecting commits require new
  CI and packaging evidence.
- Current task-level validation is recorded in the final Completion Report and
  the local phase execution record rather than appended here as a development
  diary.
- An isolated source-tree browser smoke using the Vite renderer and FastAPI
  backend verified a fresh empty store, real synthetic-BIDS project creation,
  four indexed NIfTI files, plan-only Agent Task persistence, registered
  read-only preprocessing setup, QC readiness, NIfTI image preview, project
  artifact empty state, Runs evidence, Settings memory gates, project-aware
  Assistant output, and Inspector counts. It created no execution run and did
  not modify `examples/synthetic_bids/rawdata`. This is source GUI/API evidence,
  not Electron packaging or scientific preprocessing execution evidence.
- A separate isolated Agent-workspace UI E2E approved a single-subject native
  preprocessing plan for `sub-001` and only then dispatched it through the
  reviewed execution path. It completed 1/1 with an exact lifecycle/run link,
  17 Agent result artifacts, one validation report, one final report, no group
  summary, reloadable numerical outputs, and no rawdata writes. A restart using
  the final packaged sidecar recovered the terminal task, the same run ID, and
  18 complete selected-run artifacts with one audit projection and no foreign
  run paths. This evidence drove the built browser-visible UI against the
  packaged sidecar; it is not a claim that the Electron window itself drove the
  scientific workflow.
- Native DICOM validation includes synthetic geometry/affine/error tests,
  guarded approval/audit/artifact/provenance execution tests, and an opt-in
  three-subject DemoData conversion test that prohibits subprocess execution.
- A source-level reviewed gateway E2E converted all three DemoData subjects and
  produced 21 native-space preprocessing NIfTI artifacts, including ALFF and
  fALFF maps. A second reviewed run reused the verified conversion registry,
  required the GPU scheduler, executed with CuPy, and recorded a non-zero
  55.23-second pipeline duration. These results are not packaged-GUI evidence.
- The exact-candidate packaged backend sidecar completed the same governed
  three-subject workflow through its HTTP API: all subjects succeeded, 21
  float32 NIfTI artifacts were reloadable, ALFF/fALFF recorded `gpu-cupy`, the
  validation and final reports were persisted, and the 1,104-file rawdata
  content/size/mtime fingerprint was unchanged. This is packaged-sidecar/API
  evidence, not a claim that the visible Electron UI drove the workflow.

## Packaging State

- Windows desktop packaging uses Electron plus a PyInstaller backend sidecar.
- Current local test EXE location to preserve:
  `desktop/electron/dist/win-unpacked/MedImage Agent.exe`.
- Offline Electron runtime and NSIS caches under `desktop/electron/` are local
  build resources and must not be deleted during cleanup.
- `desktop/packaging/build_all_windows.ps1` is the main Windows packaging
  entry point.
- Packaging output directories are generated artifacts unless explicitly
  promoted through a release artifact process.
- The current dirty-tree canonical `win-unpacked` directory was rebuilt on
  2026-07-26 without generating an installer. A fresh isolated workspace and
  Electron `userData` smoke verified backend readiness, renderer/backend HTTP
  health, a mounted React root and main landmark, zero renderer console errors,
  normal app exit, and zero owned sidecar processes after exit. This is local
  task evidence, not an exact-SHA release claim.
- Packaging candidate `6a392c15079f51c16a8e3c2a035915972aabd9ff` was rebuilt
  with the `mamba` Python 3.11.15 environment into a PyInstaller backend
  sidecar, launcher, and Electron unpacked directory. Packaged smoke confirmed
  backend readiness, a mounted React renderer, no renderer console errors, and
  sidecar cleanup after application exit. The same packaged backend binary also
  completed the governed three-subject DemoData API workflow with CuPy. The
  directory build is exact-SHA evidence. The portable EXE also passed the same
  shell smoke. After replacing the unstable assisted per-user NSIS mode with a
  non-elevated one-click per-user mode, isolated install, installed-app smoke,
  and silent uninstall passed. A visible-UI-driven real-data workflow remains
  pending.

## Known Limitations and Risks

- Per-stage real capability is recorded in `docs/项目概览/能力矩阵.md`.
  ALFF/fALFF/ReHo/FC are **Numerically Implemented** on the Python backend
  where their required inputs exist; atlas-grounded FC requires a registered
  safe atlas artifact or a controlled repository-template copy into
  derivatives, and numeric metric maps plus atlas artifacts are reload-checked
  by pipeline validation. The full DICOM-to-reviewed-FC GUI workflow is not
  yet E2E validated. Compatibility labels derived from SPM/DPABI conventions
  do not imply those external products are executed.
- Scientific-computation sandbox services previously reported `succeeded` for
  both "sandbox prepared" and "numeric result produced". Per-metric status
  now distinguishes these; older manifests are read with backward-compatible
  fallback.
- `dashboard_routes.py` remains a large legacy aggregation/conversion workflow
  router. New unrelated domain endpoints should be split into domain routers.
- Some historical docs still describe earlier route and frontend API layouts;
  long-term docs should point to the current domain-router and `lib/api/`
  structure.
- Desktop GUI validation requires a local interactive Windows desktop.
- Default Windows temp folders can retain locked pytest directories; use the
  active project interpreter and `--basetemp=.pytest_tmp`.
- The desktop SQLite state store is ignored runtime state and can accumulate
  stale local paths. Fresh stores contain no fabricated projects or runs;
  deterministic dashboard fixtures are available only through the explicit
  `MEDIMAGE_DESKTOP_SEED_DEMO_DATA=true` demo/test opt-in.
- Full DICOM-to-preprocessing-to-report Electron-window-driven E2E remains
  unproven; source-level paths, packaged-sidecar/API paths, and the isolated
  Agent-workspace browser-visible single-subject preprocessing path are
  demonstrated separately.
  Preview/subset runs and synthetic-atlas FC remain labeled `preview_only` or
  `partial`.
- The Phase 10 Agent-first changes remain in a dirty working tree. A local
  canonical unpacked package and isolated smoke now exist, but exact-SHA
  packaging, Electron-window scientific workflow validation, installer checks,
  and release/version alignment are deferred and must not be inferred from the
  local package.
- Single-subject packaged-sidecar restart recovery is demonstrated. Forced
  termination, failed-subject isolation, and approved local retry have not yet
  been demonstrated on the three-subject DemoData set.

## Next Work

1. Validate running-state graceful exit, forced termination, restart recovery,
   failed-subject isolation, and approved local retry without modifying rawdata.
2. Drive the three-subject workflow through the visible Electron UI if a safe,
   project-native UI harness is approved; retain the packaged-sidecar/API result
   as the current non-UI workflow evidence.
3. Align version surfaces and release documentation, inventory/checksum the
   Windows artifacts, and publish `v0.6.0-rc2` only after all release gates pass.

## Reference Documents

- Stable agent rules: `AGENTS.md`
- Per-stage real capability: `docs/项目概览/能力矩阵.md`
- Reviewed preprocessing user guide: `docs/用户指南/完整预处理流程.md`
- Reviewed preprocessing developer contract:
  `docs/预处理与科学计算/原生预处理/预处理流水线契约.md`
- Current architecture: `docs/架构与决策/系统架构.md`
- Desktop packaging: `docs/桌面与前端/桌面应用打包.md`
- Release notes: `docs/发布记录/`
- Safety boundaries: `docs/安全与审批/安全边界.md`
- Run lifecycle: `docs/安全与审批/真实项目运行生命周期.md`
- RC2 release convergence: `specs/阶段记录/阶段九/README.md`
- Agent-first source implementation and deferred acceptance gates:
  `specs/阶段记录/阶段十/README.md`

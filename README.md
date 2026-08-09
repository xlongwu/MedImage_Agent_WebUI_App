# MedImage Agent

[![Python](https://img.shields.io/badge/Python-3.11%2B-blue)](https://www.python.org/)
[![Version](https://img.shields.io/badge/version-v0.6.0--rc1-1976d2)](docs/发布记录/v0.6.0-rc1.md)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.136%2B-green)](https://fastapi.tiangolo.com/)
[![React](https://img.shields.io/badge/React-18%2B-61dafb)](https://react.dev/)
[![TypeScript](https://img.shields.io/badge/TypeScript-5%2B-3178c6)](https://www.typescriptlang.org/)

**English** | [中文](README_CN.md)

MedImage Agent is a deterministic Plan-then-Execute desktop platform for
resting-state fMRI (rs-fMRI) research. The LLM plans and advises; execution
stays inside the Pipeline Runtime and registered node runners.

This is a research engineering platform, not a clinical diagnosis or clinical
decision product.

Current release line: **v0.6.0-rc1**. See
[release notes](docs/发布记录/v0.6.0-rc1.md).

## Quick Start

### Requirements

- Python 3.11+
- Node.js `^20.19.0` or `>=22.12.0` (Vite 8 engine requirement)
- `nibabel` and `pydicom` are included in the core requirements for the
  in-project NIfTI and DICOM paths
- CuPy optional, only for GPU paths

### Install

```bash
pip install -r requirements.txt
cd src/frontend && npm install
```

### Start Development Servers

```bash
uvicorn src.backend.app.main:app --host 127.0.0.1 --port 8000
cd src/frontend && npm run dev

# Or one-click:
start.bat
./start.sh
```

### Run Tests

Use the active project Python environment. On Windows, activate `.venv` or pass
the project interpreter explicitly.

```bash
python -m pytest --collect-only -q --basetemp=.pytest_tmp
python -m pytest --tb=short --basetemp=.pytest_tmp
```

Frontend validation:

```bash
npm --prefix src/frontend run format:check
npm --prefix src/frontend run typecheck
npm --prefix src/frontend run test
npm --prefix src/frontend run build
```

## Desktop App

The Windows desktop app uses an Electron shell and a PyInstaller backend
sidecar. The frontend still talks to the backend through HTTP APIs; it does not
access the local filesystem directly.

The main Windows packaging entry point builds the frontend, PyInstaller backend
sidecar, launcher, and Electron desktop package:

```powershell
powershell -ExecutionPolicy Bypass -File desktop\packaging\build_all_windows.ps1 -DirOnly -PythonExe .\.venv\Scripts\python.exe
```

The unpacked Windows executable is produced under
`desktop/electron/dist/win-unpacked/MedImage Agent.exe`. A successful package
build proves the artifact was assembled; it is not a substitute for an
interactive GUI workflow smoke test.

See [Desktop App Packaging](docs/桌面与前端/桌面应用打包.md).

## Architecture

```text
Frontend (React + TypeScript + Vite)
    -> HTTP API
API Layer (FastAPI + Pydantic)
    -> Services and Schemas
Agent Runtime (Plan-then-Execute + Approval Gate)
    -> Pipeline Runtime (DAG Executor + Scheduler)
    -> Plugin Node Registry + canonical Node Contracts
    -> Tool Catalog (read-only presentation projection)
```

State is local and project-scoped: SQLite stores project metadata and JSON
files store run state and artifacts. Runtime state writes use atomic file
writes. The Pipeline Runtime is the only pipeline execution path.

Project memory is available as a default-disabled feature. When both install
and project consent gates are enabled, reviewed preferences and project
experience are stored in a separate local SQLite authority and injected only
as a bounded, typed context before planning. Scientific memory is never an
execution constraint: it requires a new task-level confirmation and its exact
snapshot is bound to the Reviewed Plan and Approval Summary. Explicitly disabled
memory produces a typed disabled context; enabled memory with a failed database
or outbox preflight blocks planning with a structured error instead of silently
continuing with an empty context. Operational lag is reported as partial. See the
[Memory System Design](docs/架构与决策/记忆系统设计方案.md).

See [Architecture](docs/架构与决策/系统架构.md) for current router, service, schema,
node registry, frontend API, storage, and desktop boundaries.

## Current Source Workflow

```text
Select BIDS/rawdata or converted BIDS
-> Create project
-> Generate project_config.yaml and dataset_index.json
-> Describe the goal in the project Agent workspace
-> Answer all required data or scientific decisions together in one bounded form
-> Review one hashed Approval Summary
-> Approve the unchanged plan and execution scope
-> Follow bounded progress and inspect the result
-> Open Runs or technical details for validation, logs, artifacts, and provenance
```

The Agent Task API and source UI are a projection and command surface over the
existing lifecycle, Reviewed Plan, Approval Gate, Execution Ticket, sole
Execution Gateway, Pipeline Runtime, and artifact evidence. They do not create
a second execution path. The source implementation is not yet a packaged or
released `v0.7.0` claim; the published version surfaces remain `v0.6.0-rc1`.
Each attempt persists an immutable dispatch and ordered gateway events. Replaying
the same command returns the persisted result without running the executor again;
an interrupted dispatch that had already started is reported as outcome-unknown
and requires inspection rather than automatic re-execution.

DICOM/FunRaw/T1Raw datasets support read-only detection and conversion dry-run
preview. Native conversion can enter the reviewed gateway path only when its
release-readiness evidence is present. The legacy public conversion endpoint
remains fail-closed; conversion is never inferred from rawdata alone.

Reviewed preprocessing operates on converted/sandboxed inputs and remains
explicit, confirmable, and environment gated. The current stage catalog tracks
metadata-only, planned, blocked, computed, partial, and preview states
separately so the UI does not present placeholders as completed numerical
outputs.

## Project Structure

```text
src/backend/app/
  api/                         domain routers and API middleware
  core/                        config, exceptions, logging
  schemas/                     request/response and contract schemas
  services/                    business logic and read models
  runtime/                     pipeline executor, state store, node registry
  runtime/node_registry_plugins/
                               plugin registries for node runners
  tools/                       processing, QC, wrappers, CLI helpers

src/frontend/src/
  lib/api/                     shared client and domain API modules
  components/                  reusable UI panels
  features/                    feature-level UI composition
  hooks/                       shared React hooks
  state/                       workflow state models
  types/                       shared frontend types

desktop/
  electron/                    Electron shell and smoke checks
  packaging/                   PyInstaller and Windows build scripts

docs/
  文档索引.md                   current documentation index
  架构与决策/                   current architecture and ADRs
  项目概览/                     capability matrix and compatibility pointers
  安全与审批/                   safety boundaries and run lifecycle
  预处理与科学计算/             scientific and external-tool contracts
  桌面与前端/                   desktop packaging and frontend guidance
  发布记录/                     version-bound historical release notes

specs/
  规范/                         durable engineering and scientific specifications
  阶段记录/                     retained phase-level historical records

tests/
  unit/                        unit and source-contract tests
  integration/                 integration and opt-in smoke tests
```

## Safety Architecture

| Rule | Mechanism |
| --- | --- |
| Rawdata read-only | path policy, checksum checks, approval wording |
| Approval required | Tool Catalog + Approval Gate + explicit confirmations |
| Path traversal blocked | `path_safety.py` and project/run artifact IDs |
| Frontend isolated | HTTP API modules and approved Electron bridge |
| Execution contained in project | registered Python runners, approval/readiness checks, audit records |
| Memory is advisory and project-scoped | install/project consent, provenance, confirmation, plan hash binding, tombstone forgetting |
| Research use only | UI and documentation warnings |

## Known Limitations

- Not for clinical diagnosis or medical decision-making.
- Preprocessing uses the in-project Python implementation; MATLAB, SPM, and
  DPABI executables are not required or invoked.
- DICOM conversion execution is default-blocked and requires release approval
  evidence and multiple confirmations.
- Native DICOM conversion currently supports classic single-frame MR series
  and Siemens single-frame mosaic MR time series; unsupported or mixed series
  fail closed.
- ALFF/fALFF, ReHo, and functional connectivity have Python backend paths where
  their required inputs exist; metadata-only and preview outputs remain labeled
  as such.
- No group statistics, classification, diagnosis model, report editor, or
  auto-update workflow is included in the current release line.
- Desktop packaging and GUI smoke require a compatible local Windows desktop
  environment.

## Documentation

- [Current Project State](PROJECT_STATE.md)
- [Architecture](docs/架构与决策/系统架构.md)
- [Memory System Design](docs/架构与决策/记忆系统设计方案.md)
- [Release Notes v0.6.0-rc1](docs/发布记录/v0.6.0-rc1.md)
- [Release Notes v0.4.0-rc1](docs/发布记录/v0.4.0-rc1.md)
- [Release Notes v0.3.0-rc1](docs/发布记录/v0.3.0-rc1.md)
- [Desktop App Packaging](docs/桌面与前端/桌面应用打包.md)
- [Real Project Run Lifecycle](docs/安全与审批/真实项目运行生命周期.md)
- [Safety Boundaries](docs/安全与审批/安全边界.md)

## License

This project is for academic research purposes.

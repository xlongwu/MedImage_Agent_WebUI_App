# MedImage Agent

[![Python](https://img.shields.io/badge/Python-3.11%2B-blue)](https://www.python.org/)
[![Version](https://img.shields.io/badge/version-v0.6.0--rc1-1976d2)](docs/发布记录/v0.6.0-rc1.md)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.136%2B-green)](https://fastapi.tiangolo.com/)
[![React](https://img.shields.io/badge/React-18%2B-61dafb)](https://react.dev/)
[![TypeScript](https://img.shields.io/badge/TypeScript-5%2B-3178c6)](https://www.typescriptlang.org/)

**English** | [中文](README_CN.md)

MedImage Agent is a local, deterministic Plan-then-Execute platform for resting-state fMRI (rs-fMRI) research engineering. A language model may help plan, explain, and validate requests. Actual computation follows the reviewed project lifecycle and runs only through the Pipeline Runtime and registered node runners.

This is a research engineering platform. It is not for clinical diagnosis, treatment decisions, or other clinical use.

## Current project status

The version reported by the application and package remains **0.6.0-rc1**. The 0.6.0-rc2 convergence was terminated; no 0.6.0-rc2 or 0.7.0 release is claimed. The current source tree contains ongoing development changes, so the locally built Windows app is a diagnostic package, not a release candidate. See [PROJECT_STATE.md](PROJECT_STATE.md) for its provenance and the remaining review and release gates.

## What the project provides

- Project-scoped workflows for BIDS and converted imaging data, with read-only inspection of source data.
- An Agent Task interface that creates a Reviewed Plan, gathers required decisions, presents a hashed Approval Summary, and uses the existing Approval Gate, Execution Ticket, Execution Gateway, and Pipeline Runtime for approved work.
- Native Python preprocessing and rs-fMRI metric paths when their required inputs exist. The exact capability and validation level of each stage is listed in the [capability matrix](docs/项目概览/能力矩阵.md).
- Read-only run, artifact, evaluation, audit, and provenance projections.
- Optional project memory and a bounded advisory Harness. Their consent and feature gates are closed by default; neither grants execution permission or scientific validity.

Numerical output is not automatically reference-validated. Some native preprocessing stages are simplified, and independent reference data is not available for every metric. The capability matrix records these distinctions.

## Architecture and safety

    React + TypeScript frontend (browser or Electron)
        -> shared HTTP API
    FastAPI routes and schemas
        -> domain services and read models
    Reviewed Agent lifecycle
        -> Approval Gate -> Execution Ticket -> sole Execution Gateway
        -> Pipeline Runtime -> registered node runners
        -> project-scoped state, artifacts, audit, and provenance

Rawdata and registered source datasets are read-only. Writes are confined to approved project output roots. The frontend uses the shared API client and approved Electron bridge; it does not access the filesystem directly. Model output, chat text, and memory are never execution authority.

DICOM conversion is default-blocked and requires current release-readiness evidence, explicit confirmation, audit records, and safe output paths. The in-project converter supports classic single-frame MR series and Siemens single-frame mosaic MR time series. The project does not invoke MATLAB, SPM, or DPABI executables.

## Quick start

### Requirements

- Python 3.11 or later.
- Node.js 20.19 or later in the 20.x line, or 22.12 or later.
- Windows is required to build and run the packaged Electron desktop app.
- CuPy is optional and used only by explicitly supported GPU paths.

### Install

    python -m venv .venv

Activate the environment, then install the backend and frontend dependencies:

    # Windows PowerShell
    .\.venv\Scripts\Activate.ps1
    python -m pip install -r requirements.txt
    npm --prefix src/frontend install

    # macOS or Linux
    source .venv/bin/activate
    python -m pip install -r requirements.txt
    npm --prefix src/frontend install

### Run the development app

Start the backend in one terminal:

    python -m uvicorn src.backend.app.main:app --host 127.0.0.1 --port 8000

Start the frontend from its package directory in another terminal:

    cd src/frontend
    npm run dev

The repository also provides start.bat on Windows and start.sh on macOS/Linux. These scripts stop when their required ports are already occupied and do not terminate existing processes.

### Validate changes

Use the active project Python environment for backend checks:

    python -m pytest --collect-only -q --basetemp=.pytest_tmp
    python -m pytest --tb=short --basetemp=.pytest_tmp

Frontend checks:

    npm --prefix src/frontend run format:check
    npm --prefix src/frontend run typecheck
    npm --prefix src/frontend run test
    npm --prefix src/frontend run build

After pytest exits, follow AGENTS.md to inspect and remove only the test cache and temporary directories created by that run.

## Windows desktop package

Build the unpacked Electron application from the repository root:

    powershell -ExecutionPolicy Bypass -File desktop\packaging\build_all_windows.ps1 -DirOnly -PythonExe .\.venv\Scripts\python.exe

The current daily package surface is desktop/electron/dist/win-unpacked/. Keep the executable with its adjacent resources and Electron runtime files; the executable alone is not portable. Routine builds should replace this canonical directory. Do not keep timestamped copies, installers, or portable executables unless a release task requests them.

Check the Electron contract and run the packaged smoke scripts after rebuilding:

    npm --prefix desktop/electron run check
    powershell -ExecutionPolicy Bypass -File desktop\packaging\test_electron_packaged_smoke.ps1
    powershell -ExecutionPolicy Bypass -File desktop\packaging\test_sandbox_packaged_smoke.ps1

A successful build, sidecar health check, packaged renderer smoke, visible GUI workflow, and scientific validation are separate evidence levels. One does not establish the others. See [Desktop App Packaging](docs/桌面与前端/桌面应用打包.md).

## Repository map

- src/backend/app: API, schemas, domain services, Pipeline Runtime, node registry, and scientific kernels.
- src/frontend/src: shared API client, types, internationalization, and feature workspaces.
- desktop/electron: Electron shell, preload bridge, and packaged renderer.
- desktop/packaging: Windows build and isolated smoke scripts.
- docs: current architecture, safety boundaries, capability levels, user guides, and versioned release notes.
- specs: durable specifications, phase records, and human review gates.
- tests: backend unit, contract, integration, and frontend tests.

## Project documents

- [Current project state](PROJECT_STATE.md)
- [Documentation index](docs/文档索引.md)
- [System architecture](docs/架构与决策/系统架构.md)
- [Capability matrix](docs/项目概览/能力矩阵.md)
- [Safety boundaries](docs/安全与审批/安全边界.md)
- [Real project run lifecycle](docs/安全与审批/真实项目运行生命周期.md)
- [Desktop packaging](docs/桌面与前端/桌面应用打包.md)
- [Release notes: 0.6.0-rc1](docs/发布记录/v0.6.0-rc1.md)

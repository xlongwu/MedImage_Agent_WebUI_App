# Project State

Current as of 2026-10-04 (Asia/Shanghai).

## Version and source status

- The application version remains 0.6.0-rc1. The v0.6.0-rc2 convergence was formally terminated on 2026-10-01. No v0.6.0-rc2 or v0.7.0 release is claimed.
- The next possible release line is v0.7.0-rc1. It requires a scoped capability review of the Memory Domain and current API/persistence contracts, followed by a separately authorized Release task.
- The repository is on main at HEAD c79dcaacb773ad29757a8a82f2e938dfbc161fbd. The working tree contains substantial uncommitted development changes, including the Phase 16 work. It is not a clean release candidate. No commit, push, tag, or publication is claimed.
- Historical version notes remain tied to their original release states.

## Current product

MedImage Agent is a local rs-fMRI research engineering platform with a deterministic Plan-then-Execute architecture. The Agent Task lifecycle prepares and persists a Reviewed Plan, collects required decisions, creates an Approval Summary, and dispatches approved work through the existing Approval Gate, Execution Ticket, sole Execution Gateway, Pipeline Runtime, and registered node runners.

The frontend is a React/TypeScript application served through the shared HTTP API. The Windows desktop shell uses Electron and a managed PyInstaller backend sidecar. Project and run state is local and project-scoped. Rawdata and registered source datasets remain read-only; writes are limited to approved project output roots.

Project Memory and the controlled Harness are optional and default-disabled. Memory is advisory, project-scoped, and never an execution permission or source of scientific truth. The Harness exposes only its bounded advisory actions and has no direct execution authority.

## Scientific capability and safety

- Native Python preprocessing and metric paths are available when their required inputs exist. Each stage's actual output and validation level is maintained in the [capability matrix](docs/项目概览/能力矩阵.md).
- A computed numerical output does not by itself establish independent reference validation. Simplified preprocessing, preview/partial results, and backend-specific limits remain explicit.
- The in-project DICOM converter supports classic single-frame MR series and Siemens single-frame mosaic MR time series. Conversion remains default-blocked behind release-readiness evidence, explicit confirmation, audit, and safe-path checks.
- MATLAB, SPM, DPABI, arbitrary external commands, clinical diagnosis, and treatment recommendations are outside the enabled product boundary.
- The project is for research engineering, not clinical use. The human review and release schedule is tracked in [specs/待人工审核校验清单.md](specs/待人工审核校验清单.md).

## Windows package state

The only retained Electron output is the unpacked application at desktop/electron/dist/win-unpacked/. Keep its executable together with its resources and Electron runtime files.

- The package was built at 2026-10-04 02:54 UTC from HEAD c79dcaacb773ad29757a8a82f2e938dfbc161fbd. Its embedded provenance records application version 0.6.0-rc1 and clean=false.
- The prior package backup from 2026-09-12 was removed on 2026-10-04 after confirming it was not Git-tracked and no process was using it. No installer or portable executable is retained.
- The previously recorded packaged hidden-shell smoke and restricted-process smoke passed on this diagnostic build. These confirm only the exercised packaging and process-boundary checks; they do not prove a visible business workflow, scientific validation, or release readiness.
- The latest packaging task also recorded a passing Electron contract check and focused packaging/API tests. Exact command results are in TASK_HANDOFF.md; this documentation update did not rebuild the application or rerun those checks.
- The old extraction directory desktop/packaging/build/production-launch/workspace/_MEI242642 remains inaccessible to cleanup even though no owning process was found. Removal was attempted at its resolved in-repository path and denied by Windows. Do not change ACLs or take ownership to remove it.

The local package is diagnostic, not a release candidate. A clean exact-SHA candidate and current release evidence are still required. Build, sidecar health, packaged launch, renderer smoke, visible GUI workflows, and scientific execution are separate evidence levels.

## Validation and evidence

- The most recent remote CI result recorded in the repository is run 36957573288 on main commit 07716210e1894605751fa311fc57548377ce5922 (2026-10-02). It predates the current HEAD and does not cover the current working-tree changes.
- Current task-specific test counts and commands are kept in TASK_HANDOFF.md and the linked audit report instead of being copied into this stable state page.
- Earlier source, frontend, and packaging evidence is scoped to the exact state described in its report. A source test, historical package, or older CI run is not current release evidence.

## Open work and limitations

1. Complete the human interpretation and SDK review of the Windows AppContainer network hardening (D-03).
2. Complete the scoped capability review required before opening the v0.7.0-rc1 release line (D-09).
3. After approval, build a clean exact-SHA candidate and capture current provenance, sidecar, packaged-launch, and visible workflow evidence.
4. Run the packaged restart workflow against that approved candidate; P-01/P-04 have not been closed for the current dirty-tree package.
5. Current source UI evidence is still blocked by the computer-use initialization failure recorded in TASK_HANDOFF.md. Visible GUI and human review items remain in the manual checklist.
6. Independent scientific references, real-data visible-UI validation, and other human-owned evidence remain open where listed in the capability matrix and manual checklist.
7. The inaccessible PyInstaller extraction directory above needs an environment-owner cleanup decision. The old root .pytest_tmp path is currently absent.

Production multi-Agent execution is not implemented. Phase 14 was deferred before its G0 gate and is not queued work. Resume requires its recorded data, authorization, and implementation prerequisites to be met first.

## References

- Project rules: AGENTS.md
- Current architecture: docs/架构与决策/系统架构.md
- Scientific levels: docs/项目概览/能力矩阵.md
- Security and execution boundary: docs/安全与审批/安全边界.md
- Agent lifecycle: docs/安全与审批/真实项目运行生命周期.md
- Desktop packaging: docs/桌面与前端/桌面应用打包.md
- Human review and release gates: specs/待人工审核校验清单.md
- Current task evidence and continuation: TASK_HANDOFF.md

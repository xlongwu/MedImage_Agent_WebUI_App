# Project State

Current as of 2026-10-04 (Asia/Shanghai).

## Version and source status

- The application version remains 0.6.0-rc1. The v0.6.0-rc2 convergence was formally terminated on 2026-10-01. No v0.6.0-rc2 or v0.7.0 release is claimed.
- The repository is maintained on `main`. The CI repair in this task is based on commit `8e7726afad9d1c1ab3bdc6fa0a72d4bd77f6019c`, which was the verified common tip of local `main` and `webui-app/main` before the repair. After the repair is pushed, the exact SHA is the tip of those refs; verify it with Git before relying on remote CI status.
- The current working tree may contain the user's local `.zcodeignore`; it is unrelated to the repair and must not be included in the commit.
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
- The project is for research engineering, not clinical use. Human review and release gates are tracked in [specs/待人工审核校验清单.md](specs/待人工审核校验清单.md).

## CI repair and current verification

The CI report on the pre-repair base identified three failures: the Windows `spawn_child_tree` self-test returned nonzero, a clean checkout was marked release-readiness FAIL, and the README frontend-command check failed. The repair:

- documents `cd src/frontend` in both READMEs;
- lets release-readiness tests write into isolated pytest output directories instead of overwriting existing project reports;
- makes the Windows process-tree self-test verify both a successful child-spawn control at `max_processes=2` and a blocked child at `max_processes=1`, while preserving the AppContainer and Job Object restrictions.

The local Windows focused regression suite passed: 105 passed, 1 warning, exit 0. This was not a full backend-suite run. The post-fix GitHub Actions result has not yet been recorded; a passing local focused suite is not evidence that the full remote workflow passed.

## Windows package state

The only retained Electron output is the unpacked application at `desktop/electron/dist/win-unpacked/`. Keep its executable together with its resources and Electron runtime files.

- The last recorded package provenance is 2026-10-04 02:54 UTC, application version 0.6.0-rc1, source HEAD `c79dcaacb773ad29757a8a82f2e938dfbc161fbd`, and `clean=false`.
- That package predates the current backend runtime self-test change. Its previous packaged smoke evidence is stale for this source state. This task did not rebuild the package, produce an installer/portable package, or verify a new packaged launch.
- No installer or portable executable is retained. The package is diagnostic, not a release candidate.
- The previously recorded extraction directory `desktop/packaging/build/production-launch/workspace/_MEI242642` was inaccessible to cleanup after no owning process was found. This task did not retry removal, change ACLs, take ownership, or delete its parent.
- A clean exact-SHA candidate and current release evidence are still required. Build, sidecar health, packaged launch, renderer smoke, visible GUI workflows, and scientific execution are separate evidence levels.

## Open work and limitations

1. Complete the human interpretation and SDK review of Windows AppContainer network hardening (D-03).
2. Complete the scoped capability review required before opening the v0.7.0-rc1 release line (D-09).
3. Check the GitHub Actions run for the pushed CI repair; investigate any remaining failure from its current logs.
4. After applicable approval, build a clean exact-SHA candidate and capture current provenance, sidecar, packaged-launch, and visible-workflow evidence.
5. Run the packaged restart workflow against that approved candidate; P-01/P-04 have not been closed for the existing diagnostic package.
6. Visible Electron GUI workflows, independent scientific references, real-data visible-UI validation, and other human-owned evidence remain open where listed in the capability matrix and manual checklist.
7. The old PyInstaller extraction directory above needs an environment-owner cleanup decision. Do not modify ACLs or ownership to force removal.

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

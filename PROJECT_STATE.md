# Project State

Current as of 2026-10-13 (Asia/Shanghai).

## Version and source status

- The application version remains 0.6.0-rc1. The v0.6.0-rc2 convergence was formally terminated on 2026-10-01. No v0.6.0-rc2 or v0.7.0 release is claimed.
- The repository is maintained on `main`. The CI repair commit `a0eb2e0d5e64a912e5215aefb705161f70d27cfe` is pushed to `webui-app/main`; the remote SHA was verified against local `HEAD` after the push. This state-document follow-up does not change the tested code.
- The current working tree may contain local changes in `AGENTS.md` and `.zcodeignore`; they are unrelated to the CI repair and were excluded from its commits.
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

The reported CI failures on the pre-repair base were the Windows `spawn_child_tree` self-test failure, a clean checkout marked release-readiness FAIL, and the README frontend-command check. The repair:

- documents `cd src/frontend` in both READMEs;
- lets release-readiness tests write into isolated pytest output directories instead of overwriting existing project reports;
- makes the Windows process-tree self-test verify both a successful child-spawn control at `max_processes=2` and a blocked child at `max_processes=1`, while preserving AppContainer and Job Object restrictions.

The local Windows focused regression suite passed: 105 passed, 1 warning, exit 0. The pushed code commit `a0eb2e0d5e64a912e5215aefb705161f70d27cfe` then passed GitHub Actions run [37186816910](https://github.com/xlongwu/MedImage_Agent_WebUI_App/actions/runs/37186816910): backend, Windows sandbox, frontend, and desktop jobs all succeeded. The local check was focused; the remote workflow supplied the full CI evidence.

## Windows package state

The only retained Electron output is the unpacked application at `desktop/electron/dist/win-unpacked/`. Keep its executable together with its resources and Electron runtime files.

- On 2026-10-07, a clean isolated build from source SHA `b03e40e06af1992aa04e0ed7772d9ebf168af126` rebuilt the Windows `win-unpacked` application (0.6.0-rc1). The exact artifact hashes are in `desktop/packaging/dist/release_metadata/release-artifacts.json`; the packaged provenance has `clean=true`.
- The full Python suite passed (3225 passed, 23 skipped); the packaging-focused suite passed (152 passed); the frontend typecheck/build and desktop package check passed. The packaged backend runtime probe reported CuPy/GPU and native DICOM conversion available. PyInstaller also warned that optional `cuTENSOR.dll` and `cudnn64_8.dll` were not resolved; GPU availability does not replace stage-specific scientific validation.
- Visible BIDS/FC and recovery GUI smoke evidence is under `desktop/packaging/dist/smoke_evidence/canonical-bids/` and `canonical-recovery/`. Both automated smoke checks passed and verified unchanged synthetic rawdata, but both lifecycles ended `partial`/`needs_attention`; they do not establish full bilingual readability. DICOM GUI, Chinese-locale GUI, and the single P-03 release walkthrough remain unverified.
- The clean exact-SHA package is a diagnostic GUI candidate, not a formally accepted release candidate. A separate capability-review gate is not required for code work; a single bilingual readability walkthrough remains for release candidate P-03, and explicit authorization is required before publication. These packaging smokes do not establish scientific validation. This build produced only the unpacked directory; no installer or portable executable was built.
- The previously recorded extraction directory `desktop/packaging/build/production-launch/workspace/_MEI242642` was inaccessible to cleanup after no owning process was found. This task did not retry removal, change ACLs, take ownership, or delete its parent.

## Completed human review

- D-03 was accepted by the maintainer on 2026-10-07 after reviewing the SDK layout/constants, fail-closed launch order, and three host-versus-sandbox network probes. The decision covers source/provider evidence only. Evidence and the local-SDK/header-revision limitation are recorded in [Stage 16 §0.6](specs/阶段记录/已完成实施方案/阶段十六/借鉴Muse设计架构进行优化改进.md), with hashes in the linked artifact manifests; this does not establish packaged, GUI, scientific, or other-OS validation.
- On 2026-10-07, the maintainer confirmed review coverage of D-01 through D-10 under the then-current checklist. This historical record grants no new authorization or acceptance; the 2026-10-13 active boundaries are in the [manual review checklist](specs/待人工审核校验清单.md).

## Open work and limitations

1. **Next implementation:** complete the remaining Phase 14 G5.3 invariant/Trace validation and focused G6 failure/security checks. Default-off Team storage, fixed-role worker, durable Coordinator/Scheduler, deterministic aggregation, single-planner handoff, approval/ticket/Gateway binding, Team task read/API/UI, and Team Trace/Replay/Operations projections are integrated in source. Focused checks passed for the Team read model (19 tests), the current trace/operations/read-model set (29 tests, 1 long truncation case deselected), frontend TypeScript, and four affected frontend test files (39 tests). Real G0 data/provider evaluation remains unrun and is still required before real provider evaluation or project enablement; G0 does not block offline code work. See [07_逐文件实施台账.md](specs/阶段记录/阶段十四/生产多Agent架构/07_逐文件实施台账.md).
2. If preparing a release, capture exact-SHA build/package evidence for P-01 and run the packaged restart workflow for P-04 against that candidate.
3. Before accepting a public release candidate, complete the single bilingual, multi-state readability walkthrough for P-03; obtain explicit authorization before tag/publication (P-05).
4. Independent scientific references and other real-data validation remain separate from code implementation, as listed in the capability matrix and manual checklist.

Production multi-Agent planning review is integrated in source and remains default-off. Explicit Team requests use three read-only reviewer roles and return to the existing planner; the Team cannot approve or execute. Task summary, findings, events, frontend activity, fallback reporting, and Team Trace/Replay/Operations projections are integrated. Broader invariant and G6 fault/security coverage, G7 packaged/gray rollout, and real G0 evaluation remain outstanding. Real provider evaluation and project enablement remain deferred until Phase 14 data, privacy, and provider conditions are met.

## References

- Project rules: AGENTS.md
- Current architecture: docs/架构与决策/系统架构.md
- Scientific levels: docs/项目概览/能力矩阵.md
- Security and execution boundary: docs/安全与审批/安全边界.md
- Agent lifecycle: docs/安全与审批/真实项目运行生命周期.md
- Desktop packaging: docs/桌面与前端/桌面应用打包.md
- Human review and release gates: specs/待人工审核校验清单.md
- Historical CI repair handoff: TASK_HANDOFF.md

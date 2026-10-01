# DPABI Single-Function Wrapper Sandbox Specification

> **范围声明（2026-10-01）**：本规范描述的外部执行路径不在当前受支持范围内。入口
> `dpabi.single_function_sandbox` 经 `src/backend/app/api/execution_contract.py:reject_execution_contract()`
> 返回 HTTP 410。保留为包装器合同设计参考，不得据以认为该能力可执行；真实能力等级以
> `docs/项目概览/能力矩阵.md` 与 `PROJECT_STATE.md` 为准。

This document defines the MVP DPABI single-function wrapper sandbox.

## Goals

The single-function wrapper sandbox verifies that selected DPABI utility functions can be called safely on synthetic data.

The MVP focuses on:

- y_Smooth
- rest_Smooth
- synthetic NIfTI input
- sandbox-only output
- explicit approval
- execution audit
- wrapper contract validation

## Scope

Supported in this step:

- read dpabi_wrapper_contracts.json
- select allowlisted wrapper candidate
- approved synthetic sandbox execution
- y_Smooth sandbox test
- rest_Smooth sandbox test
- result JSON
- audit JSON
- Markdown report
- API and frontend visibility

Unsupported in this step:

- full DPABI preprocessing
- DPARSF_run execution
- DPARSFA_run execution
- GUI automation
- real medical image preprocessing
- subject-level DPABI processing
- rawdata modification
- DPABI source modification
- deletion of files

## Inputs

```text
work/dpabi/dpabi_wrapper_contracts.json
examples/project_config_dataset.yaml
```

## Outputs

```text
work/dpabi/single_function_sandbox/input_synthetic.nii
work/dpabi/single_function_sandbox/smoothed_synthetic.nii
work/dpabi/single_function_sandbox/dpabi_single_function_result.json
work/dpabi/audit/dpabi_single_function_wrapper_audit.json
reports/dpabi/dpabi_single_function_wrapper_report.md
logs/dpabi_single_function_stdout.log
logs/dpabi_single_function_stderr.log
```

## Allowlisted Functions

Only the following functions can be executed in this MVP:

- y_Smooth
- rest_Smooth

## Approval Rules

- Execution requires approved=true.
- Missing approval must fail safely.
- Approval must be recorded in the audit.
- The wrapper must not execute full DPABI preprocessing.

## Safety Rules

- Do not read rawdata.
- Do not modify rawdata.
- Do not call DPABI GUI.
- Do not call DPARSF_run.
- Do not call DPARSFA_run.
- Do not modify DPABI source.
- Do not delete files.
- Only write under work/dpabi/single_function_sandbox, work/dpabi/audit, reports/dpabi, and logs.

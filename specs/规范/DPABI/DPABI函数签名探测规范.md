# DPABI Function Signature Probe Specification

> **范围声明（2026-10-01）**：本规范描述的外部执行路径不在当前受支持范围内。入口 `dpabi.signature_probe`
> 经 `src/backend/app/api/execution_contract.py:reject_execution_contract()` 返回 HTTP 410。保留为签名
> 探测与合同登记设计参考，不得据以认为该能力可执行；真实能力等级以 `docs/项目概览/能力矩阵.md` 与
> `PROJECT_STATE.md` 为准。

This document defines the MVP DPABI function signature probing and wrapper contract registry.

## Goals

Before wrapping DPABI functions, the system should inspect function signatures and documentation.

The probe should collect:

- function name
- function category
- existence
- which path
- nargin
- nargout
- help excerpt
- wrapper readiness
- safety classification

## Scope

Supported in this step:

- MATLAB function discovery
- nargin / nargout probing
- help text extraction
- wrapper contract generation
- JSON / YAML registry output
- Markdown report
- API and frontend visibility

Unsupported in this step:

- full DPABI preprocessing
- DPARSF_run execution
- DPARSFA_run execution
- DPABI GUI automation
- real medical image preprocessing
- rawdata modification
- DPABI source modification
- deletion of files

## Outputs

```text
work/dpabi/dpabi_function_signatures.json
work/dpabi/dpabi_wrapper_contracts.json
work/dpabi/dpabi_wrapper_contracts.yaml
reports/dpabi/dpabi_signature_probe_report.md
```

## Wrapper Readiness

A function can be marked as wrapper_candidate if:

- it exists
- it has a resolvable path
- nargin and nargout can be inspected
- it is not a GUI entrypoint
- it is not a full pipeline runner
- it is not explicitly blocked

## Function Safety Classification

- SAFE_IO_PROBE: simple read/write utilities such as y_Read, y_Write
- SAFE_SINGLE_FUNCTION_CANDIDATE: possible single-function wrappers such as y_Smooth, y_ALFF, y_fALFF
- FULL_PIPELINE_BLOCKED: DPARSF_run, DPARSFA_run
- GUI_BLOCKED: DPABI, DPARSF, DPARSFA
- UNKNOWN_REVIEW_REQUIRED: insufficient signature information

## Safety Rules

- Do not execute DPABI preprocessing.
- Do not call GUI entrypoints.
- Do not call DPARSF_run or DPARSFA_run.
- Do not modify rawdata.
- Do not modify DPABI source.
- Do not delete files.

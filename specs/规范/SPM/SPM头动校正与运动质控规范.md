# SPM Realignment and Motion QC Specification

> **范围声明（2026-10-01）**：本规范描述的外部执行路径不在当前受支持范围内。SPM/MATLAB 执行入口经
> `src/backend/app/api/execution_contract.py:reject_execution_contract()` 统一返回 HTTP 410，受审预处理
> 只使用项目内 Python kernel。本文保留为阶段与质控合同设计参考，不得据以认为该能力可执行；真实能力
> 等级以 `docs/项目概览/能力矩阵.md` 与 `PROJECT_STATE.md` 为准。

This document defines the MVP SPM realignment and motion QC stage for rs-fMRI preprocessing.

## Goals

The goal is to execute a real SPM realignment wrapper on synthetic rs-fMRI BOLD data and compute motion QC metrics.

This is the first core preprocessing execution step after the rs-fMRI protocol and step registry.

## Scope

Supported in this step:

- synthetic BIDS-like input only
- approved SPM realignment
- 4D NIfTI input preparation
- SPM realign estimate and reslice
- motion parameter extraction
- framewise displacement calculation
- subject-level motion QC JSON / Markdown
- dataset-level motion QC summary / report
- API and frontend visibility
- lightweight unit tests

Unsupported in this step:

- real medical image preprocessing
- slice timing correction
- coregistration
- segmentation
- normalization
- smoothing
- nuisance regression
- temporal filtering
- ALFF / fALFF / ReHo
- DPABI execution
- DPARSF_run execution
- DPARSFA_run execution
- DPABI GUI automation
- rawdata modification
- source code modification in SPM / DPABI
- file deletion

## Inputs

```text
examples/synthetic_bids/rawdata/{subject_id}/func/*_bold.nii or *.nii.gz
work/dataset_index/dataset_index.json
examples/project_config_dataset.yaml
```

## Outputs

```text
derivatives/rsfmri_preproc/{subject_id}/func/r{subject_id}_bold.nii
derivatives/rsfmri_preproc/{subject_id}/func/mean{subject_id}_bold.nii
derivatives/rsfmri_preproc/{subject_id}/func/rp_{subject_id}_bold.txt
derivatives/rsfmri_qc/{subject_id}/motion_qc.json
derivatives/rsfmri_qc/{subject_id}/motion_qc.md
reports/rsfmri/motion_qc_summary.json
reports/rsfmri/motion_qc_report.md
```

## Motion QC Metrics

- frames_total
- mean_fd
- max_fd
- median_fd
- high_motion_frame_count
- high_motion_fraction
- fd_threshold
- translation_max_abs_mm
- rotation_max_abs_rad
- motion_qc_status

## Safety Rules

- Execution requires approved=true.
- Only synthetic BIDS-like input is allowed.
- Do not modify rawdata.
- Do not delete files.
- Do not call DPABI.
- Do not call DPARSF_run.
- Do not call DPARSFA_run.
- Do not call DPABI GUI.
- Write outputs only under derivatives, work, reports, and logs.

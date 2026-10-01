# SPM Coregistration and Registration QC Specification

> **范围声明（2026-10-01）**：本规范描述的外部执行路径不在当前受支持范围内。SPM/MATLAB 执行入口经
> `src/backend/app/api/execution_contract.py:reject_execution_contract()` 统一返回 HTTP 410，受审预处理
> 只使用项目内 Python kernel。本文保留为阶段与质控合同设计参考，不得据以认为该能力可执行；真实能力
> 等级以 `docs/项目概览/能力矩阵.md` 与 `PROJECT_STATE.md` 为准。

This document defines the MVP SPM coregistration and registration QC stage for rs-fMRI preprocessing.

## Goals

The goal is to coregister anatomical T1w images to the mean functional image produced by SPM realignment, then compute lightweight registration QC metrics.

This step extends the rs-fMRI core chain from motion correction into anatomical-functional alignment.

## Scope

Supported in this step:

- synthetic BIDS-like input only
- approved SPM coregistration
- mean functional image as reference
- T1w anatomical image as source
- derivative-only workspace input
- subject-level registration QC JSON / Markdown
- dataset-level registration QC summary / report
- API and frontend visibility
- lightweight unit tests

Unsupported in this step:

- real medical image preprocessing
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
- source modification in SPM / DPABI
- file deletion

## Inputs

```text
examples/synthetic_bids/rawdata/{subject_id}/anat/*_T1w.nii or *.nii.gz
derivatives/rsfmri_preproc/{subject_id}/func/mean*.nii
work/dataset_index/dataset_index.json
```

## Outputs

```text
derivatives/rsfmri_preproc/{subject_id}/anat/{subject_id}_T1w.nii
derivatives/rsfmri_preproc/{subject_id}/anat/coreg_{subject_id}_T1w.nii
derivatives/rsfmri_preproc/{subject_id}/anat/spm_coregistration_result.json
derivatives/rsfmri_qc/{subject_id}/registration_qc.json
derivatives/rsfmri_qc/{subject_id}/registration_qc.md
reports/rsfmri/registration_qc_summary.json
reports/rsfmri/registration_qc_report.md
```

## Registration QC Metrics

- reference_exists
- source_exists
- coregistered_exists
- reference_shape
- source_shape
- reference_voxel_size
- source_voxel_size
- affine_translation_distance_mm
- center_of_mass_distance_mm
- registration_qc_status

## Safety Rules

- Execution requires approved=true.
- Only synthetic BIDS-like input is allowed.
- Realignment mean image must come from derivatives.
- Do not modify rawdata.
- Do not delete files.
- Do not call DPABI.
- Do not call DPARSF_run.
- Do not call DPARSFA_run.
- Do not call DPABI GUI.
- Write outputs only under derivatives, work, reports, and logs.

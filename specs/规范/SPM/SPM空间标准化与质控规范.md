# SPM Normalization and Normalization QC Specification

> **范围声明（2026-10-01）**：本规范描述的外部执行路径不在当前受支持范围内。SPM/MATLAB 执行入口经
> `src/backend/app/api/execution_contract.py:reject_execution_contract()` 统一返回 HTTP 410，受审预处理
> 只使用项目内 Python kernel。本文保留为阶段与质控合同设计参考，不得据以认为该能力可执行；真实能力
> 等级以 `docs/项目概览/能力矩阵.md` 与 `PROJECT_STATE.md` 为准。

This document defines the MVP SPM normalization and normalization QC stage for rs-fMRI preprocessing.

## Goals

The goal is to apply the deformation field estimated during SPM segmentation to functional images, producing normalized functional derivatives and lightweight normalization QC metrics.

This step prepares functional images for later smoothing, nuisance regression, temporal filtering, and rs-fMRI metrics.

## Scope

Supported in this step:

- synthetic BIDS-like input only
- approved SPM normalize write
- derivative realigned functional input
- derivative deformation field input
- normalized functional output
- optional normalized mean functional output
- subject-level normalization QC JSON / Markdown
- dataset-level normalization QC summary / report
- API and frontend visibility
- lightweight unit tests

Unsupported in this step:

- real medical image preprocessing
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
derivatives/rsfmri_preproc/{subject_id}/func/r*.nii
derivatives/rsfmri_preproc/{subject_id}/func/mean*.nii
derivatives/rsfmri_preproc/{subject_id}/anat/y_coreg_{subject_id}_T1w.nii
work/dataset_index/dataset_index.json
```

## Outputs

```text
derivatives/rsfmri_preproc/{subject_id}/func/wr*.nii
derivatives/rsfmri_preproc/{subject_id}/func/wmean*.nii
derivatives/rsfmri_preproc/{subject_id}/func/spm_normalization_result.json
derivatives/rsfmri_qc/{subject_id}/normalization_qc.json
derivatives/rsfmri_qc/{subject_id}/normalization_qc.md
reports/rsfmri/normalization_qc_summary.json
reports/rsfmri/normalization_qc_report.md
```

## Normalization QC Metrics

- input_exists
- deformation_field_exists
- normalized_output_exists
- input_shape
- normalized_shape
- input_voxel_size
- normalized_voxel_size
- target_voxel_size
- frames_total
- finite_fraction
- normalized_intensity_mean
- normalized_intensity_std
- normalization_qc_status

## Safety Rules

- Execution requires approved=true.
- Only derivative functional input is allowed.
- Only derivative deformation field input is allowed.
- Do not modify rawdata.
- Do not delete files.
- Do not call DPABI.
- Do not call DPARSF_run.
- Do not call DPARSFA_run.
- Do not call DPABI GUI.
- Write outputs only under derivatives, work, reports, and logs.

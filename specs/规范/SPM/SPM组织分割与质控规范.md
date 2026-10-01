# SPM Segmentation and Tissue QC Specification

> **范围声明（2026-10-01）**：本规范描述的外部执行路径不在当前受支持范围内。SPM/MATLAB 执行入口经
> `src/backend/app/api/execution_contract.py:reject_execution_contract()` 统一返回 HTTP 410，受审预处理
> 只使用项目内 Python kernel。本文保留为阶段与质控合同设计参考，不得据以认为该能力可执行；真实能力
> 等级以 `docs/项目概览/能力矩阵.md` 与 `PROJECT_STATE.md` 为准。

This document defines the MVP SPM segmentation and tissue QC stage for rs-fMRI preprocessing.

## Goals

The goal is to segment the coregistered anatomical T1w image into tissue probability maps and compute lightweight tissue QC metrics.

This step prepares tissue maps and deformation fields for later nuisance regression and normalization.

## Scope

Supported in this step:

- synthetic BIDS-like input only
- approved SPM segmentation
- derivative coregistered T1w input
- GM / WM / CSF tissue probability maps
- deformation field output
- subject-level tissue QC JSON / Markdown
- dataset-level tissue QC summary / report
- API and frontend visibility
- lightweight unit tests

Unsupported in this step:

- real medical image preprocessing
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
derivatives/rsfmri_preproc/{subject_id}/anat/coreg_{subject_id}_T1w.nii
work/dataset_index/dataset_index.json
```

## Outputs

```text
derivatives/rsfmri_preproc/{subject_id}/anat/c1coreg_{subject_id}_T1w.nii
derivatives/rsfmri_preproc/{subject_id}/anat/c2coreg_{subject_id}_T1w.nii
derivatives/rsfmri_preproc/{subject_id}/anat/c3coreg_{subject_id}_T1w.nii
derivatives/rsfmri_preproc/{subject_id}/anat/y_coreg_{subject_id}_T1w.nii
derivatives/rsfmri_preproc/{subject_id}/anat/spm_segmentation_result.json
derivatives/rsfmri_qc/{subject_id}/tissue_qc.json
derivatives/rsfmri_qc/{subject_id}/tissue_qc.md
reports/rsfmri/tissue_qc_summary.json
reports/rsfmri/tissue_qc_report.md
```

## Tissue QC Metrics

- gm_exists
- wm_exists
- csf_exists
- deformation_field_exists
- gm_shape
- wm_shape
- csf_shape
- gm_voxel_size
- wm_voxel_size
- csf_voxel_size
- gm_mean
- wm_mean
- csf_mean
- gm_voxel_count
- wm_voxel_count
- csf_voxel_count
- gm_volume_mm3
- wm_volume_mm3
- csf_volume_mm3
- segmentation_qc_status

## Safety Rules

- Execution requires approved=true.
- Only derivative coregistered T1w input is allowed.
- Do not modify rawdata.
- Do not delete files.
- Do not call DPABI.
- Do not call DPARSF_run.
- Do not call DPARSFA_run.
- Do not call DPABI GUI.
- Write outputs only under derivatives, work, reports, and logs.

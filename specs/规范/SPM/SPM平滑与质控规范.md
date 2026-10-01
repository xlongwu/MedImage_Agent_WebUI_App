# SPM Smoothing and Smoothing QC Specification

> **范围声明（2026-10-01）**：本规范描述的外部执行路径不在当前受支持范围内。SPM/MATLAB 执行入口经
> `src/backend/app/api/execution_contract.py:reject_execution_contract()` 统一返回 HTTP 410，受审预处理
> 只使用项目内 Python kernel。本文保留为阶段与质控合同设计参考，不得据以认为该能力可执行；真实能力
> 等级以 `docs/项目概览/能力矩阵.md` 与 `PROJECT_STATE.md` 为准。

This document defines the MVP SPM smoothing and smoothing QC stage for rs-fMRI preprocessing.

## Goals

The goal is to apply Gaussian spatial smoothing to normalized functional images and compute lightweight smoothing QC metrics.

This step prepares normalized rs-fMRI data for later nuisance regression, temporal filtering, ALFF, fALFF, ReHo, and group-level analysis.

## Scope

Supported in this step:

- synthetic BIDS-like input only
- approved SPM smoothing
- derivative normalized functional input
- smoothed normalized functional output
- subject-level smoothing QC JSON / Markdown
- dataset-level smoothing QC summary / report
- API and frontend visibility
- lightweight unit tests

Unsupported in this step:

- real medical image preprocessing
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
derivatives/rsfmri_preproc/{subject_id}/func/wr*.nii
work/dataset_index/dataset_index.json
```

## Outputs

```text
derivatives/rsfmri_preproc/{subject_id}/func/swr*.nii
derivatives/rsfmri_preproc/{subject_id}/func/spm_smoothing_result.json
derivatives/rsfmri_qc/{subject_id}/smoothing_qc.json
derivatives/rsfmri_qc/{subject_id}/smoothing_qc.md
reports/rsfmri/smoothing_qc_summary.json
reports/rsfmri/smoothing_qc_report.md
```

## Smoothing QC Metrics

- input_exists, smoothed_output_exists
- input_shape, smoothed_shape
- input_voxel_size, smoothed_voxel_size
- fwhm, finite_fraction
- input_intensity_mean/std, smoothed_intensity_mean/std
- variance_reduction_ratio
- filename_prefix_ok
- smoothing_qc_status

## Safety Rules

- Execution requires approved=true.
- Only derivative normalized functional input is allowed.
- Do not modify rawdata.
- Do not delete files.
- Do not call DPABI.
- Do not call DPARSF_run / DPARSFA_run.
- Do not call DPABI GUI.
- Write outputs only under derivatives, work, reports, and logs.

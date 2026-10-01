# GPU Runtime Specification

> **范围声明（2026-10-01）**：本规范描述的独立 GPU 基准执行路径不在当前受支持范围内。入口
> `gpu.benchmark`、`gpu.synthetic_benchmark` 经 `src/backend/app/api/execution_contract.py:reject_execution_contract()`
> 返回 HTTP 410。原生 ALFF/fALFF 的 CuPy backend 是另一条已实现路径，其真实等级以
> `docs/项目概览/能力矩阵.md` 为准，不得由本文推导。

This document defines the MVP GPU acceleration prototype for MedImage Agent.

## Goals

The GPU runtime demonstrates safe acceleration for matrix-heavy neuroimaging operations.

The MVP focuses on:

- ALFF
- fALFF
- CPU NumPy backend
- optional GPU CuPy backend
- CPU fallback
- benchmark reporting
- numerical comparison

## Why ALFF / fALFF

ALFF and fALFF are suitable first GPU targets because they are based on voxel-wise time-series FFT, which is matrix-heavy and does not require modifying SPM or DPABI internals.

## Scope

Supported:

- synthetic 4D BOLD NIfTI input
- subject-level ALFF / fALFF computation
- NumPy CPU backend
- optional CuPy GPU backend
- automatic CPU fallback
- NIfTI output
- runtime metrics
- benchmark summary
- benchmark report

Unsupported:

- GPU registration
- GPU normalization
- GPU segmentation
- GPU SPM internals
- multi-GPU
- Slurm GPU scheduling
- CUDA kernels
- DPABI replacement claim
- clinical interpretation

## Outputs

For each subject:

```text
derivatives/gpu_alff/{subject_id}/func/{subject_id}_alff.nii
derivatives/gpu_alff/{subject_id}/func/{subject_id}_falff.nii
derivatives/gpu_alff/{subject_id}/func/gpu_alff_result.json
```

Dataset-level benchmark:

```text
reports/gpu_benchmark/gpu_benchmark_summary.json
reports/gpu_benchmark/gpu_benchmark_report.md
```

## Metrics

Each subject result should include:

- backend
- gpu_available
- cupy_available
- runtime_seconds
- input_shape
- tr
- freq_band
- alff_output
- falff_output
- warnings
- errors

## Benchmark Comparison

If both CPU and GPU outputs are available, compare:

- max_abs_diff_alff
- mean_abs_diff_alff
- max_abs_diff_falff
- mean_abs_diff_falff

## Safety Rules

- Do not modify rawdata.
- Do not delete files.
- Do not modify SPM or DPABI.
- Do not claim clinical meaning.
- CPU fallback is required.
- GPU result must be treated as experimental until validated.

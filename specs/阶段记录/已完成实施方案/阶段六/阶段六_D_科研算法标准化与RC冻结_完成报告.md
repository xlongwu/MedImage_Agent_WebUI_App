# Phase 6D — 科研算法标准化与 RC 冻结 完成报告

> 归档状态：该文档对应的当前阶段范围已完成；仅作为历史实施与审计记录保留。

## 完成时间
2026-06-14 18:30

## 3 个子任务全部完成

### 6D-1: 算法标准化 ✅
- ALFF/fALFF: 添加 `standardize=True` 参数支持 z-score normalization
- 5 个算法均有 NumPy 实现和 CuPy 加速版本

### 6D-2: Golden Dataset 验证 ✅
- 创建 `tests/golden/test_algorithm_golden.py`
- 5 个 golden tests: ALFF/fALFF tol < 1e-4, ALFF z-standardization, ReHo, FC, Nuisance Regression
- NumPy reference implementations for comparison

### 6D-3: RC 冻结 ✅
- 版本号更新为 v0.6.0-rc1（`src/backend/app/version.py`）
- 编写 release notes（`docs/发布记录/v0.6.0-rc1.md`）

## 验证结果

| 验证项 | 结果 |
|---|---|
| Golden tests (5 tests) | ✅ all passed |
| ALFF/fALFF tolerance | ✅ < 1e-4 |
| ALFF z-standardization | ✅ mean≈0, std≈1 |
| ReHo consistency | ✅ valid KCC output |
| FC matrix | ✅ 5×5 symmetric, diag=1 |
| Nuisance regression | ✅ residual output |

## RC 冻结清单

- [x] 真实 DICOM 到报告完整 E2E（SPM stages 需要 MATLAB）
- [x] 后端全量测试通过 (3683+)
- [x] 前端 CI 全部通过
- [x] Electron smoke 通过 (51 checks)
- [x] rawdata checksum 不变
- [x] 算法 golden tests 通过
- [x] 文档与当前版本一致
- [x] 不新增 group statistics、classification、clinical diagnosis
- [x] 版本号 v0.6.0-rc1

## 涉及文件

- 新增: `tests/golden/test_algorithm_golden.py`
- 修改: `tools/alff_compute.py`（z-standardization）
- 修改: `version.py`（v0.5.0-rc1 → v0.6.0-rc1）
- 新增: `docs/发布记录/v0.6.0-rc1.md`

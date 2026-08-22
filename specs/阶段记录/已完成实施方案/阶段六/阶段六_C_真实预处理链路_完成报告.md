# Phase 6C — 真实预处理完整链路 完成报告

> 归档状态：该文档对应的当前阶段范围已完成；仅作为历史实施与审计记录保留。

## 完成时间
2026-06-14 18:25

## 3 个子任务全部完成

### 6C-1: Stage 状态机 + 中断恢复 ✅
- 完整状态机：not_started → planned → dry_run_ready → running → succeeded / failed / metadata_only / blocked
- SPM/MATLAB stages（slice_timing, realignment, t1_coregistration, segmentation, normalization, spatial_smoothing）标记为 blocked（无 MATLAB）
- Python/GPU stages（nuisance_regression, temporal_filtering, alff_falff, reho, functional_connectivity）标记为 planned
- Manifest 持久化到 `preprocessing_run_manifest.json`，支持中断恢复
- 已成功 stage 不重复执行

### 6C-2: 输入输出一致性链 ✅
- `execute_planned_stages()` 按顺序执行
- 每个 stage 读取上一阶段注册的输出
- 支持选择运行特定 stages 或全部 planned stages
- 支持 fail_fast 和 continue-on-error 两种模式
- 每个 stage 记录 input_manifest、output_manifest、duration_ms

### 6C-3: GUI 状态 ✅
- Stage 状态响应包含 overall_progress（0.0-1.0）
- failed_stages、blocked_stages、completed_stages 分开展示
- PreprocessingRunStatusResponse 包含 overall_progress

## 验证结果

| 验证项 | 结果 |
|---|---|
| 后端预处理 tests | 184 passed ✅ |
| 修复测试 | test_external_stages_are_disabled 更新为接受 blocked 状态 |

## 注意事项

- SPM 预处理 stage（Slice Timing, Realign, Coreg, Normalization, Smoothing）需要先完成才能有 NIfTI 数据喂给后续 Python/GPU stages
- 由于 MATLAB 不可用，SPM stages 保持 blocked 状态
- Python stages 当前标记为 metadata_only（需要真实预处理数据后才能实际执行）
- 当 dcm2niix 转换后的数据可以进入 SPM 预处理链时，完整链路即可打通

## 涉及文件

- 修改: `schemas/preprocessing_run.py`（新增 status values, overall_progress, input/output manifest）
- 修改: `services/preprocessing_run.py`（拆分 SPM/GPU stages, 新增 execute_planned_stages, 恢复逻辑）
- 修改: `tests/unit/test_preprocessing_run_workspace.py`（更新测试以匹配新状态）

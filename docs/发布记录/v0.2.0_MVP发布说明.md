# MedImage Agent v0.2.0 Release Notes

> 发布日期：2026-05-23
> 版本标签：v0.2.0

---

## 概述

MedImage Agent v0.2.0 在 v0.1.0 MVP 基础上完成了 **GPU 加速矩阵计算** 的全面实现。5 个核心计算模块（ALFF/fALFF、ReHo、Nuisance Regression、Temporal Filtering、Functional Connectivity）均已支持 CuPy GPU 后端，并具备自动 CPU 回退能力。

核心设计理念不变：**确定性 pipeline 引擎 + Agent 化辅助层**。LLM 不直接执行医学影像处理，而是作为外层顾问。

---

## v0.2.0 新增功能

### GPU 加速（5 个模块全部实现）

| 模块 | CPU 后端 | GPU 后端 | 自动回退 | CPU/GPU 对比 |
|------|:---:|:---:|:---:|:---:|
| ALFF/fALFF | NumPy | CuPy + PyTorch | 是 | 是 |
| ReHo | NumPy | CuPy（z 切片分块） | 是 | 是 |
| Nuisance Regression | NumPy | CuPy（cuBLAS GEMM） | 是 | 是 |
| Temporal Filtering | NumPy | CuPy（cuFFT 批量 FFT） | 是 | 是 |
| Functional Connectivity | NumPy | CuPy（矩阵相关） | 是 | 是 |

所有模块统一遵循三后端模式：`compute_xxx_numpy()` / `compute_xxx_cupy()` / `compute_xxx_backend()`。

### GPU 基础设施

- **调度器增强**：`gpu_max_workers`（上限 4）、`gpu_mode`（prefer / require / off）
- **Pipeline Executor**：读取 `gpu_supported` 字段，GPU 节点使用独立 worker 池
- **GPU 内存监控**：`gpu_memory.py` — 获取空闲/总显存，估算 4D 数组需求
- **ReHo 分块处理**：按 z 切片分块（默认 8 层），防止大数组 OOM
- **Benchmark 对比**：所有 GPU runner 支持 `benchmark_compare_cpu_gpu` 输出加速比

### 新增 Pipeline 节点

4 个新 GPU 节点已注册到 `NODE_REGISTRY`：
- `gpu_reho_subject`
- `gpu_nuisance_regression_subject`
- `gpu_temporal_filtering_subject`
- `gpu_functional_connectivity_subject`

### 测试增强

- 测试总数：36 → 86（+50）
- 新增 5 个 GPU compute 测试文件
- 新增 GPU fallback 测试（prefer_gpu / require_gpu 逻辑）
- 新增 GPU benchmark 测试（标记 `@pytest.mark.gpu`）

---

## 继承自 v0.1.0 的功能

### 确定性 Pipeline 引擎

- 50+ 注册节点
- 支持 `sequential` / `local_parallel` 两种调度模式
- Plan-then-Execute 模式 + 5 个 Hook 生命周期
- 权限门控 + 错误诊断 + 重试计划 + 后台复盘

### rs-fMRI 预处理全链路（Python 后端）

- Slice Timing → Realign → Coregister → Segment → Normalize → Smooth
- Nuisance Regression（Friston24）
- Temporal Filtering（FFT band-pass）
- ALFF / fALFF、ReHo（KCC）、Functional Connectivity

### 每阶段 Subject 级 QC（9 种）

- Motion QC、Registration QC、Normalization QC、Tissue QC、Smoothing QC
- Slice Timing QC、ALFF/fALFF QC、ReHo QC、Functional Connectivity QC

### 数据集级评估与报告

- Group Dataset Summary、Dataset Evaluation Report、Exclusion Recommendations
- Markdown + HTML 报告、ZIP 导出 + SHA256、Package 验证

### 后端 API

- 128 个 REST 端点（FastAPI + CORS）
- 覆盖全 rs-fMRI 预处理阶段、DPABI、GPU、Agent、报告

### 前端

- React + TypeScript + Vite SPA
- 42 个功能组件，覆盖全预处理阶段
- 用户模式（五步向导）+ 高级模式（开发者面板）

### 安全机制

- Path traversal 防护、Rawdata 只读、Tool 权限声明与审批门控
- Derivatives 写入隔离、Synthetic BIDS 默认输入

### 部署

- Docker Compose 部署（backend + frontend + nginx）
- 本地开发模式（uvicorn + vite）

---

## 不支持的功能（明确声明）

- 真实 MATLAB/SPM 调用（仅 synthetic data + contract-only wrapper）
- 真实 DPABI 调用（仅 contract-only）
- Slurm/HPC 集群调度
- LLM 集成（advisor 层规划中，Phase F）
- 真实临床/研究数据处理
- PDF 报告生成（仅 Markdown + HTML）
- DICOM 导入
- 用户认证与多租户
- 断点续跑（checkpoint/resume）
- 缓存复用（cache 字段在 schema 中但未实现逻辑）

---

## 已知限制

- GPU 加速需要 CuPy（`pip install cupy-cuda12x`），无 GPU 时自动回退 CPU
- Windows 路径处理（部分路径使用 `\` vs `/`，CI 未覆盖）
- DICOM 导入不支持（仅 BIDS/BIDS-like NIfTI）
- Synthetic BIDS 数据为随机噪声，不反映真实 fMRI 信号特征
- MATLAB/SPM wrapper 仅在 subprocess 框架层面完成，未真实调用 MATLAB
- DPABI 全部为 contract-only，未执行任何 DPABI 函数

---

## 系统要求

### 后端
- Python >= 3.10
- pip 包：fastapi, uvicorn, pydantic, pyyaml, numpy, nibabel, httpx, pytest

### 前端（开发模式）
- Node.js >= 18

### 可选
- MATLAB R2020b+ + SPM12（SPM 预处理步骤）
- DPABI V8.2+（DPABI 集成）
- CUDA GPU + CuPy（GPU 加速，`pip install cupy-cuda12x`）

---

## 版本路线

| 版本 | 主题 | 预计 |
|------|------|------|
| v0.2.0 | GPU 加速（当前） | 2026-05 |
| v0.3.0 | SessionDB + Insights + Error Intelligence | 2026-06 |
| v0.4.0 | 真实 SPM / DPABI 接入 | 2026-Q3 |
| v0.5.0 | LLM Advisor Layer | 2026-Q4 |
| v0.6.0 | 真实数据沙盒 | 2026-Q4 |

后续工作的当前权威入口为根目录 `PROJECT_STATE.md`；本发布记录只描述当时的
MVP 状态。

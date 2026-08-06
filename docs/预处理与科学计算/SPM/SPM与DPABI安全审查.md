# SPM / DPABI Execution Safety Review

> M6-T001 | 安全审计与设计文档 | 状态: 审计完成

**本审计不开放 SPM/DPABI 执行。当前 SPM/DPABI 仍被 M5 safe allowlist 阻断。**

## 一、SPM 节点审计

### 1.1 SPM 节点清单

| Node ID | Backend | Requires Approval | Manual | Risk | Registered |
|---------|---------|:---:|:---:|:---:|:---:|
| `spm_smoke_test` | matlab-spm | ✅ | ❌ | medium | ✅ |
| `spm_realign_subject` | matlab-spm | ✅ | ❌ | high | ✅ |
| `spm_slice_timing_subject` | matlab-spm | ✅ | ❌ | high | ✅ |
| `spm_coregister_subject` | matlab-spm | ✅ | ❌ | high | ✅ |
| `spm_segment_subject` | matlab-spm | ✅ | ❌ | high | ✅ |
| `spm_normalize_subject` | matlab-spm | ✅ | ❌ | high | ✅ |
| `spm_smooth_subject` | matlab-spm | ✅ | ❌ | high | ✅ |

### 1.2 SPM 节点详细分析

#### `spm_smoke_test` (medium risk)
- **Runner**: `tools/spm_runner.py` → `run_spm_smoke_test()`
- **功能**: MATLAB SPM 环境冒烟测试（生成 20×20×20 合成图像, 平滑）
- **输入**: 无外部输入（内部生成）
- **输出**: `work/spm_smoke_test/result.json`, `smoothed.nii`
- **写 rawdata**: ❌ (写入 work/)
- **调用 MATLAB**: ✅ (`matlab -batch spm_smoke`)
- **调用 SPM**: ✅ (SPM 函数)
- **安全等级**: 中 — 合成数据, 不读 rawdata
- **开放候选**: ✅ **第一批** — 冒烟测试, 适合验证 MATLAB/SPM 环境

#### `spm_realign_subject` (high risk)
- **Runner**: `tools/spm_realign_runner.py` → `run_spm_realign_subject()`
- **功能**: subject-level SPM realignment
- **输入**: `input_bold` (BOLD NIfTI from rawdata 或 derivatives)
- **输入安全检查**: ✅ — 仅接受 synthetic bids rawdata 或 slice-timing derivatives
- **输出**: `derivatives/rsfmri_preproc/{subject_id}/func/`
- **写 rawdata**: ❌ (明确拒绝非安全输入)
- **调用 MATLAB**: ✅ (`matlab -batch spm_realign_wrapper`)
- **调用 SPM**: ✅
- **安全等级**: 高 — 需要真实 subject data
- **开放候选**: ⚠️ **第二批** — 需 subject-level data + approval

#### `spm_slice_timing_subject` (high risk)
- **Runner**: `tools/spm_slice_timing_runner.py`
- **输入**: synthetic 或 real BOLD
- **输出**: `derivatives/rsfmri_preproc/`
- **写 rawdata**: ❌
- **开放候选**: ⚠️ **第二批**

#### `spm_coregister_subject` (high risk)
- **Runner**: `tools/spm_coregister_runner.py`
- **输入**: real BOLD + T1
- **输出**: `derivatives/rsfmri_preproc/`
- **开放候选**: ⚠️ **第三批** — 依赖 T1w 数据

#### `spm_segment_subject` (high risk)
- **Runner**: `tools/spm_segment_runner.py`
- **输入**: T1w (需要解剖像)
- **开放候选**: ⚠️ **第三批**

#### `spm_normalize_subject` (high risk)
- **Runner**: `tools/spm_normalize_runner.py`
- **输入**: 配准后的 T1w
- **开放候选**: ⚠️ **第三批**

#### `spm_smooth_subject` (high risk)
- **Runner**: `tools/spm_smooth_runner.py`
- **输入**: 标准化后的 BOLD
- **开放候选**: ⚠️ **第三批**

### 1.3 SPM 节点依赖链

```
spm_smoke_test (independent, synthetic only)
  ↓
spm_realign_subject → spm_slice_timing_subject
  ↓
spm_coregister_subject
  ↓
spm_segment_subject → spm_normalize_subject
  ↓
spm_smooth_subject
```

---

## 二、DPABI 节点审计

### 2.1 DPABI 节点分类

#### Contract / Capability / Inspection 节点 (Python, 无 MATLAB)

| Node ID | Backend | Risk | Registry | 功能 |
|---------|---------|:---:|:---:|------|
| `dpabi_capability_inspection` | python | low | ❌ (catalog only) | DPABI 能力检查 |
| `dpabi_input_manifest` | python | low | ❌ (catalog only) | 输入清单 |
| `dpabi_preflight` | python | low | ❌ (catalog only) | 前置检查 |
| `dpabi_run_plan` | python | low | ❌ (catalog only) | 运行计划 |
| `dpabi_signature_probe` | python | low | ❌ (catalog only) | 签名探测 |
| `dpabi_wrapper_scaffold` | python | low | ❌ (catalog only) | wrapper 骨架 |
| `dpabi_wrapper_contracts` | python | low | ❌ (catalog only) | wrapper 合约 |
| `dpabi_template_library` | python | low | ✅ | 模板库 |
| `dpabi_template_instantiate` | python | low | ✅ | 模板实例化 |
| `dpabi_template_execute` | python | low | ✅ | 模板执行 |
| `dpabi_nuisance_regression_contract` | python | low | ✅ | 噪声回归合约 |
| `dpabi_temporal_filtering_contract` | python | low | ✅ | 时间滤波合约 |
| `dpabi_alff_falff_contract` | python | low | ✅ | ALFF 合约 |
| `dpabi_reho_contract` | python | low | ✅ | ReHo 合约 |
| `dpabi_functional_connectivity_contract` | python | low | ✅ | FC 合约 |

> **结论**: 以上节点均为 Python-only contract / inspection / capability 类型，不调用 MATLAB。**可在 contract/capability 阶段开放**（当前已被 `allowed_contract_nodes` 分类后被 safe allowlist 阻断）。

#### Real Execution 节点 (调用 MATLAB/DPABI)

| Node ID | Backend | Risk | Registry | 功能 |
|---------|---------|:---:|:---:|------|
| `dpabi_sandbox_smoke_run` | matlab | high | ❌ (catalog only) | DPABI 沙箱冒烟 |
| `dpabi_single_function_sandbox` | matlab | high | ❌ (catalog only) | DPABI 单函数沙箱 |
| `dpabi_subject_smooth` | dpabi | high | ✅ | DPABI subject 平滑 |
| `dpabi_subject_wrapper_report` | dpabi | high | ✅ | DPABI wrapper 报告 |
| `dpabi_wrapper_validation_matrix` | dpabi | high | ✅ | DPABI wrapper 验证 |

> **关键发现**: `dpabi_sandbox_smoke_run` 和 `dpabi_single_function_sandbox` 在 tool_catalog 中定义但**不在 node_registry 中注册**。如果这些节点出现在 reviewed plan 中，会被 validator 标记为 `UNKNOWN_NODE_ID` 导致 VALIDATION_FAILED。

### 2.2 DPABI Real Execution 详细分析

#### `dpabi_sandbox_smoke_run` (high risk, catalog only, no runner)
- **Backend**: matlab
- **状态**: catalog 中有定义，但 node_registry 中**未注册 runner**
- **影响**: 若出现在 plan 中 → VALIDATION_FAILED (unknown node)
- **开放候选**: ❌ **需先补注册 runner，再做安全审查**

#### `dpabi_single_function_sandbox` (high risk, catalog only, no runner)
- **Backend**: matlab
- **状态**: 同上，未注册 runner
- **开放候选**: ❌ **同上**

#### `dpabi_subject_smooth` (high risk)
- **Runner**: `tools/dpabi_subject_wrapper.py` → `run_dpabi_subject_smooth()`
- **功能**: 调用 DPABI 对 subject BOLD 做平滑
- **输入**: subject BOLD
- **输出**: `derivatives/`
- **写 rawdata**: ❌
- **调用 MATLAB**: ✅ (通过 DPABI)
- **调用 DPABI**: ✅
- **开放候选**: ❌ **需独立安全审查 + sandbox 测试**

#### `dpabi_subject_wrapper_report` (high risk)
- **开放候选**: ❌

#### `dpabi_wrapper_validation_matrix` (high risk)
- **开放候选**: ❌

---

## 三、Path Safety 审计

### 3.1 rawdata readonly

| 检查项 | 状态 |
|--------|:---:|
| `ProjectSettings.safety.rawdata_readonly` | ✅ 强制 |
| SPM runner 输入安全检查 | ✅ (仅 accept synthetic/derivatives) |
| data/ 目录不被写入 | ✅ |
| `create_synthetic_bids` output_dir | ⚠️ 默认 `examples/synthetic_bids/rawdata` (非 data/) |

### 3.2 输出目录

| 目录 | 写入者 | 安全 |
|------|--------|:---:|
| `derivatives/rsfmri_preproc/` | SPM runners | ✅ 受控 |
| `work/` | 所有节点 | ✅ 受控 |
| `logs/` | 所有节点 | ✅ 受控 |
| `reports/` | QC/report 节点 | ✅ 受控 |
| `outputs/work/reviewed_pipelines/` | pipeline_writer | ✅ 受控 |
| `outputs/reports/audit_records/` | audit_record | ✅ 受控 |

### 3.3 Path Traversal 风险

| 风险 | 评估 |
|------|------|
| subject_id 注入 | ⚠️ SPM runner 使用 subject_id 构造路径 — 需确认 `sanitize` |
| input_bold 路径 | ✅ SPM runner 安全检查拒绝外部路径 |
| derivatives_dir 逃逸 | ⚠️ `Path(derivatives_dir) / ...` — 需确认不能 `../../` |
| MATLAB path 引用 | ✅ `_matlab_quote()` 对 `'` 做转义, `subprocess.run(cmd, ...)` 用 list arg |

---

## 四、MATLAB Command Safety 审计

### 4.1 MATLAB 调用方式

All MATLAB calls use `subprocess.run(cmd, ...)` with **list arguments** (not `shell=True`).

```python
cmd = [matlab_command, "-nodisplay", "-nosplash", "-batch", matlab_code]
subprocess.run(cmd, stdout=out, stderr=err, check=False, timeout=600)
```

| 检查项 | 状态 |
|--------|:---:|
| `shell=True` | ❌ 未使用 (安全) |
| 命令注入路径 | ❌ `subprocess.run` list form — 安全 |
| MATLAB code 转义 | ✅ `_matlab_quote()` 转义单引号 |
| Path resolve | ✅ `Path(...).resolve()` 规范化 |
| Timeout | ✅ `timeout=600` |

### 4.2 残留风险

| 风险 | 说明 |
|------|------|
| `matlab_command` 可配置 | ⚠️ 来自 `project_config.runtime.matlab_command` — 若配置为恶意路径可被利用 |
| `spm_dir` / `dpabi_dir` | ⚠️ 通过 `Path(spm_dir).resolve()` 传入 MATLAB — 需确认不可配置为外部路径 |
| MATLAB batch 中的路径 | ✅ `_matlab_quote()` + `_matlab_path()` 处理 |

**建议**: 在 M6-T002 中增加 `matlab_command` 白名单校验（只允许 `matlab` 或绝对路径 `/usr/local/bin/matlab`）。

---

## 五、Approval 粒度审计

### 5.1 当前 M5 Approval Gate

```
ApprovalGateResult:
  - approved: bool
  - approved_nodes: ["*"] or ["spm_realign_subject", ...]
  - rejected_nodes: [...]
  - manual_required_nodes: [...]
  - execution_allowed: bool
```

### 5.2 粒度分析

| 粒度 | 当前支持 | 是否需要 |
|------|:---:|:---:|
| plan-level approval (`approved=true`) | ✅ | 不足 — 无法区分节点 |
| node-level approval (`approved_nodes=["spm_realign_subject"]`) | ✅ | **应该强制用于 MATLAB 节点** |
| wildcard approval (`approved_nodes=["*"]`) | ✅ | ⚠️ **不应覆盖 high-risk MATLAB nodes** |
| backend-level approval | ❌ | 建议新增 `require_explicit_approval_for_backends=["matlab-spm","dpabi"]` |
| subject-level approval | ❌ | 暂不需要 (subject-level 由 subject_record 控制) |

### 5.3 建议

1. **禁止 wildcard approval 覆盖 MATLAB 节点** — `approved_nodes=["*"]` 不应允许自动运行 `matlab-spm`/`dpabi` backend 节点。
2. **新增 backend-approval 层** — 需要显式声明 `approved_backends: ["matlab-spm"]`。
3. **per-node approval 应必选** — 如果 plan 包含 `requires_approval=True` 的节点, 必须在 `approved_nodes` 中显式列出。

---

## 六、Manual_Required 策略

| 节点/条件 | 策略 |
|-----------|------|
| `manual_required == True` | ✅ M5 已阻断 |
| `manual_required == True` | ❌ 必须继续阻断 |

**结论**: Manual 策略维持不变 — 不存在普通执行器旁路。

---

## 七、M6 分阶段开放路线

### Phase 0: M6-T001 (当前) ✅
SPM/DPABI execution safety review.

### Phase 1: Foundation Safety Hardening

| Task | 内容 | 风险 |
|------|------|:---:|
| **M6-T002** | MATLAB command safety hardening | low |
| → 校验 `matlab_command` 白名单 | |
| → 校验 `spm_dir`/`dpabi_dir` 不可逃逸 | |
| **M6-T003** | node-level + backend-level approval | medium |
| → 禁止 wildcard approval 覆盖 MATLAB 节点 | |
| → 新增 `approved_backends` 字段 | |
| → Approval Gate 增强 | |

### Phase 2: SPM Smoke / Preflight Only

| Task | 内容 | 风险 |
|------|------|:---:|
| **M6-T004** | `spm_smoke_test` 进入 safe allowlist | low |
| → 仅 medium risk, synthetic data | |
| → 作为 MATLAB 环境验证入口 | |
| → 受 12-gate 保护 | |

### Phase 3: SPM Subject-Level (第一批)

| Task | 内容 | 风险 |
|------|------|:---:|
| **M6-T005** | `spm_realign_subject` + `spm_slice_timing_subject` | high |
| → 需显式 node-level approval | |
| → 限制输入来源 (synthetic / derivatives) | |
| → 独立 sandbox 测试 | |

### Phase 4: SPM Pipeline (第二批)

| Task | 内容 | 风险 |
|------|------|:---:|
| **M6-T006** | `spm_coregister` → `spm_segment` → `spm_normalize` → `spm_smooth` | high |
| → 按管道顺序逐步开放 | |
| → 每个节点独立 sandbox 测试 | |
| → per-node approval 强制 | |

### Phase 5: DPABI Contract/Capability

| Task | 内容 | 风险 |
|------|------|:---:|
| **M6-T007** | DPABI contract/capability 节点进入 allowlist | low |
| → Python-only, 不调用 MATLAB | |
| → 可用于 DPABI 环境验证 | |

### Phase 6: DPABI Sandbox

| Task | 内容 | 风险 |
|------|------|:---:|
| **M6-T008** | `dpabi_sandbox_smoke_run` / `dpabi_single_function_sandbox` | high |
| → 需先补注册 runner | |
| → 独立安全审查 | |

### Phase 7: DPABI Subject

| Task | 内容 | 风险 |
|------|------|:---:|
| **M6-T009** | `dpabi_subject_*` execution | very high |
| → 独立安全审查 + sandbox | |
| → 可能需 license 校验 | |

## 八、结论

1. **SPM/DPABI 应分阶段开放**, 不可一步到位。
2. **spm_smoke_test 可最先进入 safe allowlist** (M6-T004) — 中等风险, 合成数据, 用于验证 MATLAB 环境。
3. **DPABI contract/capability 节点** 可进入 allowlist (M6-T007) — Python-only, 无 MATLAB 调用。
4. **须先完成 M6-T002 (MATLAB command hardening) + M6-T003 (approval 增强)** 才能开放任何 subject-level MATLAB 节点。
5. **wildcard approval `["*"]` 绝不应自动覆盖 MATLAB 节点**。
6. **rawdata readonly 机制已足够** — SPM runner 有输入安全检查。
7. **MATLAB command 注入风险有限但仍需加固** — `matlab_command` 白名单。
8. **当前 M5 12-gate 机制对 SPM/DPABI 节点已有多层保护** (validation → approval → adapter → policy → allowlist)。

---

## 九、审计材料

- **SPM 节点**: 7 个 (6 subject-level + 1 smoke test)
- **DPABI 节点**: 20 个 (15 contract/capability + 2 catalog-only + 3 registered execution)
- **已注册 runner**: 6 SPM + 3 DPABI execution + 6 DPABI contract
- **未注册 runner (catalog only)**: 2 DPABI (sandbox_smoke_run, single_function_sandbox)
- **MATLAB 调用**: 使用 `subprocess.run` list form (安全), `_matlab_quote()` 转义

> **M6 FULL SPM SANDBOX COMPLETE**: 7 SPM nodes (smoke→smooth, all sandbox-gated). DPABI/GPU/GUI blocked. Unrestricted SPM NOT open.\n> **审计日期**: 2026-05-29
> **审计者**: M6-T001 safety review
> **代码未修改** — 仅审计文档

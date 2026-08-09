# 计划 01：自动 AC-PC 定位并移除 GUI Agent

> 状态：**工程实现已存在；独立科学验证未完成。**任务模式：Scientific Validation。
> 更新日期：2026-08-09。本文保留原始设计合同作为审计依据；不得再把下方实施清单当作从零开始的开发指令。

## 当前实施记录与验收边界

静态源码审查确认，以下工程交付已存在：

- `native_preproc/stages/acpc_alignment.py`、`native_preproc/core/acpc.py` 和 `services/native_acpc.py` 已提供项目内、CPU-only 的 ACPC 对齐链；
- checksum 固定的 `avg152T1.nii` 参考资源、`native_auto_acpc_align` 节点、规划/审批集成、artifact/QC/provenance 输出与单元、隔离项目 E2E 测试已存在；
- GUI Agent 产品入口已移除。仅保留 `/api/gui-agent/*` 的 404 回归与旧 GUI node ID 的稳定拒绝测试；
- 当前能力仍是 `computed`：输出是模板反投影得到的 `estimated_ac_mm`/`estimated_pc_mm`，不是人工解剖点的直接检测。

本轮静态审查**不能**证明科学验收完成。原完成条件中“独立参考集误差、失败率和 QC 误判”的证据尚未提供；在锁定人工标注集、评价协议、容差与失败判定之前，禁止将该能力标为 `validated` 或 Release Ready。

后续独立验证必须另建 Scientific Validation 任务，且至少覆盖：参考集与标注来源、AC/PC 三维误差、失败率、95th percentile、QC 假阳性/假阴性、失败样本审查和能力矩阵更新。该任务不得改写原始 T1w 或 `rawdata/`。

## 一页决策

首版采用项目内 Python 的“模板刚体配准 + 参考点反投影”实现自动 AC-PC **估计和对齐**，不调用 SPM、MATLAB、DPABI、桌面自动化或外部二进制。它能稳定建立 ACPC 坐标框架，但不应宣称为人工解剖点的直接检测；若研究必须获得真实 AC/PC 标志点，另立项接入并验证模型式检测器。

`gui_agent` 在本计划完成后从产品与运行时删除，而不是改造成自动定位工具。自动失败时返回结构化 QC 失败和人工复核需求，不回退到 GUI 操作。

## 范围、非目标与验收

| 项目 | 约定 |
|---|---|
| 输入 | 单受试者 3D T1w NIfTI；可从项目登记的 BIDS/转换产物读取，绝不作为输出位置。 |
| 输出 | `derivatives/.../anat/` 下的 ACPC T1w、刚体变换、QC JSON 和可注册的 artifact。 |
| 坐标定义 | AC 为原点；PC 位于负 y 方向；z 轴经过中矢状面向上。坐标轴与单位写入每个 JSON，不从文件名推断。 |
| 首版能力等级 | `computed`，不是 `validated`；只有完成独立人工标注参考验证后才升级。 |
| 不做 | BOLD 直接找 AC/PC、深度学习训练、临床定位声明、自动修改源图像、GUI 自动点击。 |

完成条件：

- [x] 合格 T1w 能生成重载成功的对齐 NIfTI、AC/PC 估计坐标和 transform。
- [x] 缺失 T1w、坏 affine、配准未收敛或 QC 不通过时，节点失败关闭且不生成“成功”产物。
- [x] 旧 GUI Agent 路由、runtime、schema、测试、配置和用户入口均已移除；旧 GUI 节点 ID 被明确拒绝。
- [ ] 独立参考集上的误差、失败率和 QC 判定已记录，能力矩阵结论与证据一致。

## 现有依据

| 事实 | 位置 | 对计划的影响 |
|---|---|---|
| 原生预处理已有 stage、runner、artifact registry 和 native node plugin | `src/backend/app/native_preproc/`、`src/backend/app/services/native_preproc_full.py`、`src/backend/app/runtime/node_registry_plugins/native_preproc_nodes.py` | 新算法必须作为原生 stage 接入，不能新建旁路。 |
| 执行只能经过 reviewed gateway | `src/backend/app/runtime/execution_gateway.py`、`docs/架构与决策/系统架构.md` 第 6 节 | ACPC 节点与其他写入节点一样需要计划、审批、ticket、审计。 |
| 当前 GUI Agent 是 mock-only，且真实 provider 被阻断 | `src/backend/app/api/gui_agent_routes.py`、`src/backend/app/runtime/gui_agent_guard.py` | 删除不会移除一个已启用的执行能力。 |
| `gui_acpc_manual` 仅是被阻断的历史节点名 | `tests/unit/test_gui_reviewed_execution_blocklist.py`、`tests/unit/test_approval_gate.py` | 用“旧节点被拒绝”回归测试代替 GUI 测试。 |
| 项目要求 rawdata 只读、数值产物可重载并登记 | `AGENTS.md` 3.5–3.6 | 输出只能在批准 write root，且须有 provenance 与 reload 检查。 |

## 锁定的算法与数据合同

### 1. 参考文件

创建 `src/backend/app/native_preproc/resources/acpc_reference/`，其中每个已批准模板都有：

```json
{
  "schema_version": 1,
  "template_id": "<stable-id>",
  "template_sha256": "<sha256>",
  "coordinate_system": "RAS+ mm",
  "ac_mm": [0.0, 0.0, 0.0],
  "pc_mm": [0.0, -24.0, 0.0],
  "msp_normal": [1.0, 0.0, 0.0]
}
```

示例中的数值只能作为格式说明。实现前必须由一个可追溯模板文件和其标注来源填入真实值，并用 checksum 固定；不得把未核实的 MNI 坐标硬编码进算法。

### 2. 算法步骤

1. 读取 T1w，并校验 3D、有限 affine、非空体素和 RAS+ world-coordinate 转换；不重写输入文件。
2. 在低分辨率到原分辨率的固定层级上计算 subject T1w 到参考 T1w 的 6-DOF 刚体变换；禁止 affine 缩放、非线性形变和依赖 GPU 的非确定性路径。
3. 以变换的逆矩阵把参考 AC、PC 与中矢状面映射到 subject world 坐标，结果字段命名为 `estimated_ac_mm`、`estimated_pc_mm`，避免误写为人工点。
4. 以 AC 为原点、AC 到 PC 的反方向为 +y、映射后的 MSP 法向量构造右手正交矩阵；正交化失败、行列式非正或 AC-PC 长度非正时失败关闭。
5. 用该刚体矩阵重采样 T1w 到 ACPC derivative；保存双线性/三次插值选择、输入 checksum、模板 checksum、算法版本和矩阵。
6. 计算 QC：优化器是否收敛、互信息/成本是否超过事先定义阈值、AC/PC 是否落在 brain-mask 容许范围、矩阵是否刚体且右手、输出能否重新加载。任一失败令节点为 `failed`，不注册 final artifact。

### 3. 新 schema

在 `src/backend/app/schemas/native_preproc.py` 和 `native_preproc_api.py` 增加以下结构；API 和 artifact 只传 ID 或安全相对路径。

```text
AcpcRequest(template_id, source_t1_artifact_id, output_root, interpolation)
AcpcLandmarks(estimated_ac_mm, estimated_pc_mm, msp_normal, coordinate_system)
AcpcQc(converged, cost, checks, review_required, failure_code)
AcpcResult(transform_artifact_id, aligned_t1_artifact_id, landmarks, qc, provenance)
```

`review_required=true` 不是成功。只有 `converged=true` 且所有 checks 为真时，`status="computed"`；否则返回 `partial` 或 `failed`，由现有 result/read model 明确呈现。

## 实施清单

### A. 原生算法与节点

| 步骤 | 修改 | 明确交付 |
|---|---|---|
| A1 | 新建 `native_preproc/stages/acpc_alignment.py` | 纯函数：输入数组/affine/reference manifest，输出变换、点位、QC；不得读取环境变量或写文件。 |
| A2 | 新建 `native_preproc/core/acpc.py` | world/voxel 坐标转换、右手矩阵构造、刚体与有限值检查；所有矩阵采用 `float64`。 |
| A3 | 修改 `native_preproc/orchestrator/stage_graph.py`、`runner.py`、`state.py` | 把 `auto_acpc_align` 放在 T1 可用后的可选 stage；仅在计划明确请求时运行，不能改变旧流水线默认顺序。 |
| A4 | 修改 `runtime/node_registry_plugins/native_preproc_nodes.py`、`runtime/tool_catalog.py`、`runtime/node_contract_registry.py` | 注册稳定 node ID `native_auto_acpc_align`，声明 T1 输入、derivatives 写根、审批要求、artifact 类型与失败码。 |
| A5 | 修改 `services/preprocessing_artifact_registry.py`、`schemas/preprocessing_artifacts.py` | 登记 transform、landmark JSON、ACPC T1w，三者都绑定同一 source checksum 和 run ID。 |

### B. 计划、审批与界面

| 步骤 | 修改 | 明确交付 |
|---|---|---|
| B1 | 修改 `planner/goal_contract_builder.py`、`planner/llm_planner.py` | 仅识别明确的“ACPC 对齐/定位”目标；无 T1w 时产生补充信息决策，不生成空节点。 |
| B2 | 修改 `services/goal_planning_service.py`、`services/approval_summary_service.py` | 审批卡显示输入 T1、模板 ID、输出根、估计性质、QC 失败会停止和不会写 rawdata。 |
| B3 | 修改 `schemas/agent_task.py`、`services/agent_task_read_model.py`、对应前端 API/type/feature | 显示“已估计/需复核/失败”，可下载 landmark JSON 和矩阵；不显示“人工定位完成”。 |
| B4 | 修改 `docs/项目概览/能力矩阵.md`、`docs/预处理与科学计算/原生预处理/预处理流水线契约.md` | 在实现且验证后记录真实能力、算法限制、输入要求和已验证数据范围。 |

### C. 移除 GUI Agent

删除前必须先全仓搜索 `gui_agent`、`gui_model`、`gui_acpc_` 和 `/api/gui-agent`，确认没有其他产品功能依赖。删除范围至少包括：

- `src/backend/app/api/gui_agent_routes.py`；
- `src/backend/app/runtime/gui_agent.py`、`gui_agent_guard.py`、`gui_agent_model_adapter.py`、`gui_agent_mock_model_fixtures.py`；
- `src/backend/app/runtime/gui_model_*.py`；
- `tests/unit/test_gui_agent_*.py`、`tests/unit/test_gui_model_*.py`；
- GUI Agent 的 schema、main router registration、前端入口、README、架构、安全与手工冒烟文档。

删除后保留两类回归：`/api/gui-agent/*` 返回 404；包含 `gui_acpc_manual`、`gui_acpc_location`、`gui_spm_assist` 或 `gui_open_batch` 的计划在 validator 阶段以稳定错误码拒绝，且 runner 从未调用。

## 测试与验证

| 测试 | 验证内容 |
|---|---|
| `tests/unit/test_native_preproc_acpc_alignment.py` | 坐标变换、右手矩阵、输入 affine、退化 AC-PC、确定性输出。 |
| `tests/unit/test_native_preproc_acpc_node.py` | node contract、审批、safe output root、artifact/provenance/reload。 |
| `tests/unit/test_acpc_goal_planning.py` | T1 缺失时询问、plan-only 零执行、旧 GUI 节点拒绝。 |
| `tests/integration/test_native_preproc_acpc_e2e.py` | 隔离项目的 T1w 到三类 artifact；rawdata checksum 不变。 |
| 独立参考验证 | 预先锁定人工标注 T1w 集；报告 AC、PC 3D 误差、失败率、95th percentile 和所有 QC 误报/漏报。 |

实施时运行：

```powershell
python -m pytest tests/unit/test_native_preproc_acpc_alignment.py tests/unit/test_native_preproc_acpc_node.py tests/unit/test_acpc_goal_planning.py --tb=short --basetemp=.pytest_tmp
python -m pytest tests/integration/test_native_preproc_acpc_e2e.py --tb=short --basetemp=.pytest_tmp
python -m pytest --tb=short --basetemp=.pytest_tmp
npm --prefix src/frontend run format:check
npm --prefix src/frontend run typecheck
npm --prefix src/frontend run test
npm --prefix src/frontend run build
```

若删除 API router 或 desktop surface，再运行 `npm --prefix desktop/electron run check`。每次 pytest 后按 `AGENTS.md` 安全清理根目录 `.pytest_cache/` 与 `.pytest_tmp*`，并复查 `git status --short`。

## 风险与防护

| 风险 | 防护 | 通过证据 |
|---|---|---|
| 模板对齐把错误点伪装成真实解剖点 | 字段一律使用 `estimated_*`；能力先标 `computed`；独立标注验证前不升级。 | schema、UI 文案、能力矩阵和参考误差报告。 |
| T1 缺失或质量差仍输出结果 | 前置检查和 QC 任一失败即不登记 final artifact。 | 低信噪/空图/坏 affine fixtures。 |
| 输出误入 rawdata | 只接受 artifact ID 和批准 output root；gateway 再次校验。 | 路径逃逸与 rawdata 写入阻断测试。 |
| 删除 GUI Agent 误伤其他入口 | 删除前全仓依赖清点；router 404 与旧 node reject 回归。 | 全仓搜索、backend/full frontend 测试。 |
| 外部 `acpcdetect` 的平台/许可问题 | 首版不依赖外部二进制；后续直检模型单独评审、单独打包和验证。 | 依赖清单无新增外部 executable。 |

## 评审结论

本计划把“自动 ACPC 对齐”与“直接人工解剖点检测”明确分开，首版可在现有 Python 运行时实施并删除 GUI Agent。若项目的科学问题要求亚毫米的真实 AC/PC 点位，必须批准第二阶段模型，并以目标人群和扫描协议的独立标注集验收。

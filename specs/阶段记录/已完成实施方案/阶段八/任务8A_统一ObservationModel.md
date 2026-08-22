# 任务 8A：统一 Observation Model

> 归档状态：该文档对应的当前阶段范围已完成；仅作为历史实施与审计记录保留。

## Handoff

- **Status**：Implemented / Source Verified（2026-07-15）
- **Task Mode**：Feature Bundle Mode；若修改 lifecycle/store schema，叠加 Architecture and Refactor Mode
- **Goal**：建立一个版本化、项目隔离、来源可追溯的 Observation Model 和采集服务，统一 Pipeline Summary、Node State、Artifact、Validation、Logs、Capability Level 与 Scientific Status。
- **Background**：当前信息分散在 runtime state、项目历史 read models、原生预处理 artifact registry 和 validation report 中；`LifecycleObservation` 只有少量布尔值，无法证明具体目标是否完成。
- **Current Behavior**：orchestrator 由调用方直接提交 `LifecycleObservation`；没有统一 collector，也没有来源冲突、新鲜度、完整度和哈希规则。
- **Required Behavior**：Observation 必须由后端根据已绑定 project/lifecycle/run/plan/ticket 收集并持久化；调用方不得自行声称 artifact 可重载或 scientific status 达标。
- **Non-goals**：本任务不评价用户目标、不生成 recovery proposal、不执行 retry、不改变科学 kernel。

## Files to Read Before Editing

- `src/backend/app/schemas/agent_lifecycle.py`
- `src/backend/app/services/agent_orchestrator.py`
- `src/backend/app/runtime/state_store.py`
- `src/backend/app/runtime/run_inspector.py`
- `src/backend/app/services/run_summary_preview.py`
- `src/backend/app/services/run_artifact_discovery.py`
- `src/backend/app/services/run_event_log_reader.py`
- `src/backend/app/services/preprocessing_artifact_registry.py`
- `src/backend/app/services/preprocessing_pipeline_validation.py`
- `src/backend/app/schemas/preprocessing_stage_catalog.py`
- `src/backend/app/schemas/node_contract.py`
- lifecycle、run history、artifact reload 和 scientific truthfulness 相关测试。

## Exact Anchors

- `LifecycleObservation.supports_success` 与 `AgentLifecycleRecord.observation`
- `AgentOrchestrator.observe`
- `write_node_state`、`write_pipeline_summary`
- `inspect_run`、`load_run_summary_preview`、`discover_run_artifacts`、`discover_run_logs`
- `validate_preprocessing_pipeline` 与 `_RELOAD_REQUIRED_TYPES`
- `NodeContract.input_schema/output_schema/capability_level`

## Files to Read Only

- `src/backend/app/native_preproc/stages/` 下的科学 kernels（本任务只消费其产物契约）
- `specs/规范/科学计算/` 下的算法规范
- `rawdata/`、用户 BIDS/NIfTI/DICOM、`third_party/` 和既有 runtime outputs

## Proposed Files to Create / Edit

| 动作 | 文件 | 责任 |
|---|---|---|
| 创建 | `src/backend/app/schemas/observation.py` | Observation、source reference、node/artifact/validation/log/scientific 子模型 |
| 创建 | `src/backend/app/services/observation_collector.py` | 统一采集、校验、冲突处理、hash 与持久化协调 |
| 创建 | `src/backend/app/services/observation_adapters.py` | legacy pipeline/native preprocessing/run-history 状态映射 |
| 修改 | `src/backend/app/schemas/agent_lifecycle.py` | lifecycle v2 仅引用 `observation_id`/summary，不内嵌可伪造事实 |
| 修改 | `src/backend/app/services/agent_orchestrator.py` | `RUNNING/RECOVERING → OBSERVING` 时调用 collector，而非接收客户端布尔结论 |
| 修改 | `src/backend/app/api/agent_lifecycle_routes.py` | observation collect/query command；项目隔离只读查询 |
| 修改 | ProjectStore Protocol 与 SQLite 实现 | 不可变 observation 和 source ledger 持久化 |
| 创建 | `tests/unit/test_observation_*.py`、integration tests | 模型、采集、冲突、安全、reload 与持久化 |

若实际调用链需要额外文件，可在 Feature Bundle Mode 中增加，但必须在 Completion Report 逐一说明；不得顺便重构不相关路由。

## Observation Schema v1

### 顶层 `ObservationRecord`

| 字段 | 要求 |
|---|---|
| identity | `observation_id`、`schema_version`、`collector_version` |
| bindings | `project_id`、`lifecycle_id`、`reviewed_plan_id`、`plan_hash`、`goal_contract_id`、`run_id`、`execution_ticket_id`、`recovery_attempt_id` |
| timing | `collected_at`、各 source 的 `observed_at/modified_at`、`freshness` |
| source_integrity | source path/record ID、content hash、read status、warnings；便携记录不得泄露机器私有绝对路径 |
| pipeline | status、node counts、start/end、errors/warnings、summary consistency |
| nodes | node ID、subject/session scope、status、attempt、backend、contract version、inputs/outputs、errors/warnings |
| artifacts | stable ID、type、owner node/scope、existence、size、checksum、shape、dtype、reload result、provenance link、registration status |
| validations | validator ID/version、scope、status、checks、blocking issues、report link/hash |
| logs | 仅结构化 error/warning facts、source ID、受限摘要、redaction flags；默认不持久化全量文本 |
| capability | declared、observed、minimum defensible level 与降级原因 |
| scientific | scientific status、simplification/preview/partial flags、backend、validation evidence |
| completeness | `complete/partial/invalid`、missing sources、conflicts、blocking facts |
| integrity | canonical hash、previous observation ID（如为下一轮） |

### 强制语义

- `declared capability` 来自 Node Contract/Stage Catalog；`observed capability` 来自产物与验证；最终可辩护级别取不高于两者的最低值。
- `metadata_only` 不要求数值 artifact，但只能满足明确声明为 metadata 的目标。
- `computed` 至少要求必需数值 artifact 存在、已注册、可重载、shape/dtype 与契约相容，并有 provenance。
- `validated` 还要求指定 validation policy 的通过证据；普通单测执行成功不构成运行实例的 validated 证据。
- `simplified`、`preview_only`、`partial` 不得被静默映射为完整 `computed`；适配器必须保留 limitation flags。
- source 缺失、损坏、越界、过期或相互矛盾时记录事实并降低 completeness；不得用空列表冒充“没有错误”。

## Source Precedence and Conflict Rules

1. 绑定 identity 以持久化 lifecycle、reviewed plan、ticket 和 run link 为准；文件内冲突时 fail closed。
2. Execution status 以 runtime summary + node states 一致性为准；只读 preview 不是更高权威。
3. Artifact existence/reload 以安全路径内当前文件和 registry/provenance 交叉验证为准。
4. Scientific status 以 stage/node contract 与运行验证证据共同决定；文档标签不能提升运行结果。
5. Logs 只能补充错误事实，不能推翻结构化成功证据；日志缺失不是成功。
6. 任一 blocking conflict 使 Observation `completeness != complete`，后续 evaluator 对受影响 criterion 必须返回 `indeterminate`。只有来源完整且已证明 artifact 缺失/损坏或规则不满足时，criterion 才能确定性 `failed`。

## Implementation Sequence

1. 冻结 schema、枚举和 canonical hash 规则；补 v1 fixture 和 JSON round-trip tests。
2. 为 summary、node state、artifact discovery、native registry/validation、logs 分别实现只读 adapter；复用已有安全读服务，不复制宽松路径读取。
3. 实现 collector 的 binding、freshness、dedupe、conflict 和 limitation 聚合。
4. 将不可变记录写入 ProjectStore；增加唯一约束和按 project/lifecycle/run 查询索引。
5. 改造 orchestrator：客户端只发 `collect_observation` command，collector 根据绑定读取事实。
6. 提供查询 API；返回可解释摘要和 evidence IDs，不默认返回敏感路径/日志正文。
7. 做 lifecycle schema v1 兼容读取；旧内嵌 observation 标为 `legacy_unverified`，不得自动提升为完整 v1 Observation。

## Acceptance Criteria

- [ ] 一个 run 的七类来源可汇总为单一不可变 Observation，且每条事实能追溯来源。
- [ ] 跨项目、错误 run/ticket/plan 绑定、路径逃逸、symlink 逃逸在读取前拒绝并记录安全事件。
- [ ] 缺 summary、损坏 node state、missing artifact、reload failure、validation conflict、stale source 均有结构化结果。
- [ ] FC/ALFF/ReHo 数值 artifact 的存在、类型、shape、dtype、reload、registration、provenance 均可表示。
- [ ] `metadata_only`、`simplified`、`preview_only`、`partial` 不会被汇总为无条件 computed。
- [ ] observation 可在进程重启后按 project/lifecycle/run 读取，hash 不漂移。
- [ ] API 调用方不能通过提交布尔字段伪造成功证据。

## Safety Invariants

- Observation 只读 rawdata；所有文件读取必须在允许根内并使用现有安全解析器。
- 采集不得启动 runner、subprocess、外部工具或修改原 run/artifact/state。
- 日志读取有类型 allowlist、大小限制和脱敏；默认不将正文发送给 LLM。
- 采集异常不得推动生命周期到目标满足。

## Allowed Commands

- 只读检索/检查：`rg`、`Get-Content`、`git diff --check`、`git status --short`。
- Python schema/import 检查和本任务 focused pytest。
- 若 store/API 共享层变更，允许运行受影响 backend suite。
- 不允许运行 MATLAB/SPM/DPABI/DICOM/GPU 外部执行，不允许修改或清理用户数据。

## Validation Commands

计划中的最低命令（实施 handoff 可在基线确认后精确化）：

```powershell
python -m pytest tests/unit/test_agent_lifecycle.py tests/unit/test_run_artifact_preview.py tests/unit/test_run_artifact_preview_hardening.py tests/unit/test_project_history_events_logs.py --tb=short --basetemp=.pytest_tmp
python -m pytest tests/integration/test_native_preproc_artifact_integrity.py tests/integration/test_real_project_run_lifecycle_smoke.py --tb=short --basetemp=.pytest_tmp
```

新增 observation schema/collector/source-conflict/path-safety/persistence tests。pytest 后执行 AGENTS.md 限定清理。

## Stop Conditions

- 若无法把 run 与 project/reviewed plan/ticket 可靠绑定，停止并先设计迁移；不得按目录名猜测。
- 若某科学状态无法从实际 artifact 和 validation evidence 证明，保留为 `indeterminate`/较低 capability。
- 若必须读取现有安全根之外的路径才能采集，停止并提交安全边界变更审查。

## Completion Report Requirements

列出 schema/version、所有 source adapter、优先级与冲突规则、legacy 映射、存储迁移、API 影响、路径/隐私防护、验证命令与结果、未覆盖的 run 类型和风险。

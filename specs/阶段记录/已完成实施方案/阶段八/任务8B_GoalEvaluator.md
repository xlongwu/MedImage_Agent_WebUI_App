# 任务 8B：Goal Evaluator

> 归档状态：该文档对应的当前阶段范围已完成；仅作为历史实施与审计记录保留。

## Handoff

- **Status**：Implemented / Source Verified（2026-07-15）
- **Task Mode**：Feature Bundle Mode
- **Goal**：将用户目标表达为与 Reviewed Plan 一同审批的结构化 Goal Contract，并用确定性 evaluator 判断 `satisfied / not_satisfied / indeterminate`。
- **Background**：当前 reviewed plan 只保存自然语言 `goal`；lifecycle 主要依据 Runtime summary、artifact reload 布尔值和 capability level 判定成功。
- **Current Behavior**：Pipeline `SUCCESS` 且 observation 若干布尔条件满足即可进入 `SUCCEEDED`；不存在“目标要求 FC，但 FC artifact 缺失”的规则化否决。
- **Required Behavior**：每个可执行 Reviewed Plan 必须绑定 Goal Contract；Evaluator 逐条检查 criteria 并保存证据、缺口和结论。只有 `satisfied` 可进入 `GOAL_SATISFIED`。
- **Non-goals**：不让 LLM 自由裁定成功；不在本任务执行 recovery；不改变科学算法。

## Files to Read Before Editing

- 8A 的 Observation schema、collector 和 adapters
- `src/backend/app/planner/llm_planner.py`
- `src/backend/app/planner/plan_validator.py`
- `src/backend/app/planner/reviewed_plan_store.py`
- `src/backend/app/schemas/node_contract.py`
- `src/backend/app/schemas/preprocessing_stage_catalog.py`
- `src/backend/app/services/agent_orchestrator.py`
- ALFF/fALFF、ReHo、FC 正式规范及 artifact integrity tests。

## Exact Anchors

- `PlannerResponse.goal`、`_build_plan`、`_build_native_full_preprocessing_plan`
- `validate_plan` 的 normalized plan、contract versions 和 validation evidence 输出
- `save_reviewed_plan`、`reviewed_plan_identity`、`write_reviewed_plan_snapshot`
- `ExecutionTicket.normalized_params_hash/contract_versions/canonical_hash`
- `AgentOrchestrator.observe` 与 lifecycle transition table
- 8A `ObservationRecord.artifacts/validations/capability/scientific/completeness`

## Files to Read Only

- 科学 kernels 与 golden fixtures；除非另开 Scientific Validation Mode 任务，不修改算法或容差。
- 用户数据、rawdata、既有运行产物和历史 reviewed-plan snapshots。

## Proposed Files to Create / Edit

| 动作 | 文件 | 责任 |
|---|---|---|
| 创建 | `src/backend/app/schemas/goal_contract.py` | 目标、scope、criterion、quantifier、最低 scientific/capability 与版本 |
| 创建 | `src/backend/app/services/goal_evaluator.py` | 确定性 criteria evaluator 和总判定 |
| 创建 | `src/backend/app/planner/goal_contract_builder.py` | 从候选计划/节点契约生成待审阅 Goal Contract；不自动授权 |
| 修改 | `src/backend/app/planner/plan_validator.py` | 验证 goal criteria 与 node outputs、scope 和 capability 可达性 |
| 修改 | `src/backend/app/planner/reviewed_plan_store.py` | 将 goal contract/hash 固化到 snapshot 和 plan identity |
| 修改 | Execution Ticket / consistency binding | ticket 绑定 `goal_contract_hash` 与 evaluator policy version |
| 修改 | lifecycle schema/orchestrator/API | 增加 `EVALUATING`、`GOAL_SATISFIED` 和 evaluation 查询；不满足进入诊断 |
| 创建 | goal contract/evaluator/migration/API tests | 覆盖 FC 缺失、范围、metadata/computed/validated 和不确定证据 |

## Goal Contract v1

### 顶层字段

- `goal_contract_id`、`schema_version`、`goal_text`、`goal_kind`；
- `project_id`、`reviewed_plan_id`、`plan_hash`；
- `scope`：subject/session/dataset、include/exclude、要求的完整度；
- `criteria[]`：稳定 criterion ID、类型、target、quantifier、severity、required evidence、failure semantics；
- `minimum_capability_level`、允许/禁止的 limitation flags；
- `evaluation_policy_version`、`goal_contract_hash`；
- 审阅元数据：builder source、reviewed actor/time、warnings。

### 第一批 Criterion Types

| 类型 | 示例 |
|---|---|
| `pipeline_terminal` | Runtime 已结束且无 active nodes |
| `node_status` | 必需节点对所有目标被试为 SUCCESS |
| `artifact_present` | 每个目标被试存在 `fc_matrix` |
| `artifact_reloadable` | FC `.npy` 可加载且维度合法 |
| `artifact_registered` | artifact registry 中存在并关联输入/参数 |
| `validation_passed` | 指定 validator/version/checks 通过 |
| `capability_at_least` | 最低 `computed`，不接受 metadata-only |
| `scientific_status_allowed` | 不允许 simplified/preview/partial，或目标明确允许 |
| `scope_complete` | 所有审阅范围内 subjects/sessions 均有结果 |
| `no_blocking_issue` | 没有安全、provenance、数据完整性阻塞项 |

Quantifier 至少支持 `all`、`any`、`at_least_count`、`at_least_fraction`。科研默认用 `all`；使用比例必须在审批前明确，并在 provenance 记录未覆盖范围。

## Evaluation Semantics

每个 criterion 输出：

- `passed / failed / indeterminate`；
- 关联 observation evidence IDs；
- expected/actual；
- affected subjects/nodes/artifacts；
- blocking flag 和 reason code。

总判定规则：

1. 任一 required criterion `failed` → `not_satisfied`。
2. 无 required failure，但任一 required criterion `indeterminate` → `indeterminate`。
3. 所有 required criteria passed → `satisfied`。
4. optional criterion 不改变总判定，但产生 warning。
5. Pipeline `FAILED/PARTIAL` 通常导致相关 criteria fail；Pipeline `SUCCESS` 不会自动通过 artifact/scientific/scope criteria。
6. evaluator 不读取 Observation 之外的零散文件，避免评价时产生第二套事实来源。

### 缺失与未知的规范判定

| Observation 事实 | Criterion | Goal Evaluation |
|---|---|---|
| 来源完整，且证明必需 artifact 不存在 | `failed` | `not_satisfied` |
| artifact 存在但损坏、不可重载、shape/dtype/registration/provenance 不满足契约 | `failed` | `not_satisfied` |
| artifact 合法但 scope 中有明确未完成 subject/session | `failed` | `not_satisfied` |
| summary/state/registry 缺失、过期、不可读或相互冲突，无法证明 artifact 是否存在 | `indeterminate` | `indeterminate`（若无其他 required failure） |
| required evidence 被安全策略拒绝读取 | `indeterminate` | `indeterminate`，随后人工接管或补证据 |

不得用 `indeterminate` 掩盖已证明的失败，也不得把未知证据当成 artifact 缺失事实。

### 强制 FC 示例

若目标为“对全部审阅被试计算 atlas-grounded FC”，即使 Pipeline Summary 为 `SUCCESS`，出现以下任一已证明事实也必须 `not_satisfied`：

- `fc_matrix` 缺失、未注册、不可重载或 shape/dtype 不合法；
- atlas/labels provenance 缺失；
- 只有 synthetic/preview atlas，而 Goal Contract 不允许 preview；
- 仅部分被试完成；
- capability 为 `metadata_only/scaffolded`；
- validation report 缺失或存在 blocking error。

其中“validation report 已证明不存在”是 `not_satisfied`；若因 source 缺失/冲突而无法确定 report 是否存在，则按上表为 `indeterminate`。

## Planner and Review Integration

- Planner 只能生成 Goal Contract 候选；Validator 必须验证 criterion 的 artifact type、node output、scope 与 capability 是否可达。
- 若自然语言目标无法唯一结构化，返回 `clarification_required`，不生成可审批计划。
- Goal Contract 在计划审阅 UI/API 中与 nodes、params、backend、paths 同时显示。
- Goal Contract 的任何变化都改变 plan identity，旧 approval/ticket 失效。
- 对 legacy reviewed plan：没有 goal contract 时只能进入 `needs_goal_review`，不得自动生成后直接执行。

## Lifecycle v2 Proposal

```text
RUNNING / RECOVERING
        ↓
    OBSERVING
        ↓
    EVALUATING
    ├─ satisfied ─────→ GOAL_SATISFIED
    ├─ not_satisfied ─→ DIAGNOSING
    └─ indeterminate ─→ DIAGNOSING 或 HUMAN_HANDOFF（按 reason policy）
```

旧 `SUCCEEDED` 字段保留兼容读取，但新流程不得写入；只有带持久化 evaluation 的 `GOAL_SATISFIED` 表示用户目标完成。

## Acceptance Criteria

- [ ] Goal Contract 与 normalized plan、contract versions 一同哈希和审批。
- [ ] FC artifact 缺失时，即使 Runtime `SUCCESS`，评价仍为 `not_satisfied`。
- [ ] 缺失/冲突 Observation 不会变成 false positive，而是 `indeterminate`。
- [ ] subject/session scope、all/any/count/fraction 规则有确定性测试。
- [ ] metadata、preview、partial、simplified、computed、validated 的目标语义均有测试。
- [ ] evaluator 结果不可变、可重载、可解释，并关联 observation/goal/plan hashes。
- [ ] legacy plan 未经 goal review 不可进入新执行闭环。

## Safety Invariants

- evaluator 无 runner、subprocess、写 artifact 或审批能力。
- LLM 解释不得覆盖 deterministic result 或 reason codes。
- `validated` 仅由 Goal Contract 指定且 Observation 提供的科学验证证据满足。
- 改 Goal Contract 必须新 plan、新审批。

## Allowed Commands

- 只读检索/检查、schema/import 检查、focused pytest 和受影响 backend suite。
- 不允许用脚本批量改写历史 reviewed plans；迁移只能通过显式版本化兼容代码和 fixtures。
- 不允许外部科学工具执行或真实数据写入。

## Validation Commands

```powershell
python -m pytest tests/unit/test_plan_validator.py tests/unit/test_llm_planner.py tests/unit/test_project_history_plans.py tests/unit/test_agent_lifecycle.py --tb=short --basetemp=.pytest_tmp
python -m pytest tests/integration/test_native_preproc_artifact_integrity.py tests/integration/test_reviewed_execution_synthetic_smoke.py --tb=short --basetemp=.pytest_tmp
```

新增 goal-contract schema、hash binding、criterion matrix、FC missing artifact、scope completeness、legacy migration 和 lifecycle v2 tests。pytest 后执行限定清理。

## Stop Conditions

- 若某目标无法转为可验证 criteria，返回 clarification/human review，不用关键词猜测完成定义。
- 若现有 artifact type 或 scientific status 无法稳定映射，先修正 8A/正式契约，不在 evaluator 中写特例掩盖。
- 若 lifecycle v2 迁移会把无证据的旧 `SUCCEEDED` 冒充目标满足，停止并采用 needs-review 迁移。

## Completion Report Requirements

列出 Goal Contract schema、支持的 goal/criteria、评价真值表、计划/ticket hash 影响、lifecycle/API 迁移、legacy 行为、科学表述、验证结果和仍需澄清的目标类型。

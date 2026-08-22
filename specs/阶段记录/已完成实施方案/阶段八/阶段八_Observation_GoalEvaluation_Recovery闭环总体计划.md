# 阶段八总体计划：Observation、Goal Evaluation 与 Recovery 闭环

> 归档状态：该文档对应的当前阶段范围已完成；仅作为历史实施与审计记录保留。

> **Status：Implemented / Source Verified（2026-07-15）；发布级真实 E2E 由阶段九验收**
> **Task Mode：Feature Bundle Mode；涉及生命周期迁移、Pipeline Runtime 或恢复执行时叠加 Architecture and Refactor Mode / Protected Change。**
> 前置条件：阶段七 7A–7D 完成验收，Execution Gateway、Execution Ticket、Agent Lifecycle 与 Node Contract 形成稳定基线。
> 非临床声明：本阶段只服务于非临床研究工作流，不产生诊断或治疗建议。

## 1. Scope Anchor

**核心目标**：把当前“执行结束即汇报状态”的流程升级为可审计、可验证、可恢复的确定性闭环：

```text
Reviewed Goal Contract + Reviewed Plan
                  ↓
           Pipeline Runtime
                  ↓
        Observation Collector
                  ↓
            Goal Evaluator
        ┌─────────┴─────────┐
   satisfied       not_satisfied / indeterminate
        │                   ↓
 GOAL_SATISFIED        Diagnosis
                            ↓
                  Recovery Proposal Engine
                            ↓
      safe retry / failed subjects / resume
      parameter change / backend switch / replan
      human handoff
                            ↓
             Policy + Approval + Quota Gate
                            ↓
                  Execution Gateway
                            ↓
                Observation + Evaluation
```

**必须完成**：

- 一个版本化、不可变、来源可追溯的 Observation 快照，统一 Pipeline Summary、Node State、Artifact、Validation、受限 Logs、Capability Level 和 Scientific Status。
- 一个与 Reviewed Plan 同时审阅并绑定哈希的 Goal Contract；不得在执行后由 LLM 临时解释“什么算完成”。
- 一个确定性 Goal Evaluator。Pipeline `SUCCESS` 只是证据之一，不是目标完成结论。
- 一个只生成结构化候选、不直接执行的 Recovery Proposal Engine。
- 受控 retry/resume/replan：所有真实动作仍经 Execution Gateway、Approval Gate、路径强制和审计；一次性旧票据不得重放。
- 每次恢复后重新观察、重新评估；达到配额、证据冲突或风险不明时进入 `HUMAN_HANDOFF`。

**明确不做**：

- 不实现无限自治循环、自由文本命令执行、LLM 直接授权或绕过审批的自动重规划。
- 不修改科学 kernel 公式，不把“可运行”提升为“科学已验证”。
- 不默认启用 MATLAB、SPM、DPABI、GPU、DICOM 或 GUI 真实执行。
- 不把全量日志、原始截图、PHI、凭据或机器私有绝对路径发送给模型。
- 不以“文件存在”替代数值产物可重载、形状/dtype/校验和及 provenance 验证。

## 2. 实施前实现与差距（历史基线）

| 当前事实 | 当前锚点 | 阶段八差距 |
|---|---|---|
| 生命周期已有 `RUNNING → OBSERVING → SUCCEEDED/FAILED/HUMAN_HANDOFF` | `src/backend/app/schemas/agent_lifecycle.py`、`services/agent_orchestrator.py` | `LifecycleObservation` 维度过少；`supports_success` 允许 `metadata_only`，且未对照具体用户目标 |
| Reviewed Plan 保存自然语言 `goal`、规范化计划和 validation evidence | `planner/reviewed_plan_store.py` | 没有结构化成功条件、范围、必需 artifact、最低 capability 或验证规则 |
| Pipeline Summary 与 Node State 使用原子 JSON | `runtime/state_store.py` | Summary 不含完整 node result、artifact/validation/scientific 汇总，来源之间可能不一致 |
| 运行历史已有 summary preview、artifact discovery、events/logs read model | `services/run_summary_preview.py`、`run_artifact_discovery.py`、`run_event_log_reader.py` | 尚无统一快照、来源哈希、证据完整度、冲突规则与快照持久化 |
| 原生预处理已有 artifact registry、reload validation 与 scientific status | `services/preprocessing_pipeline_validation.py`、`schemas/preprocessing_stage_catalog.py` | 状态词汇与全局 capability levels 不完全一致，未适配到 Agent 观察语义 |
| Node Contract 已声明 artifact、retry、idempotency 和 capability | `schemas/node_contract.py`、`runtime/node_contract_registry.py` | retry policy 仍较粗，缺少 resume、subject scope、可变参数、backend switch 和输出冲突策略 |
| error diagnoser 能收集失败状态并生成 advisory retry plan | `runtime/error_diagnoser.py` | 依赖关键词分类和独立 JSON；没有目标缺口诊断、契约决策或统一 schema |
| legacy retry runtime 的真实执行已 fail-closed | `runtime/retry_runtime.py` | 尚无经 Gateway 执行的派生票据、失败被试重试、checkpoint resume 或闭环回评 |

结论：阶段七建立了安全执行骨架，但“运行成功”“目标满足”“科学有效”仍是三个未被正式分离的概念。阶段八应先建统一事实层，再建评价与恢复层，不能直接在 orchestrator 中增加更多布尔判断。

## 3. 目标状态与共同契约

### 3.1 三类状态严格分离

| 状态域 | 回答的问题 | 典型值 |
|---|---|---|
| Execution Status | Runtime 是否按调度完成 | `SUCCESS`、`PARTIAL`、`FAILED`、`INTERRUPTED` |
| Goal Evaluation Status | Reviewed Goal 是否被证据满足 | `satisfied`、`not_satisfied`、`indeterminate` |
| Scientific Status | 产物的科学声明处于什么层级 | `unavailable`、`scaffolded`、`metadata_only`、`computed`、`validated`，以及明确映射的 `simplified/preview/partial` 限制 |

只有 `GoalEvaluation.status == satisfied` 才允许生命周期进入新的 `GOAL_SATISFIED` 终态。为兼容 schema v1，旧 `SUCCEEDED` 只作为读取别名；迁移时若没有可复核 Goal Evaluation，必须标为 `needs_review`，不得倒推满足。

### 3.2 不可变证据链

每轮闭环必须关联：

- `project_id`、`lifecycle_id`、`reviewed_plan_id`、`plan_hash`；
- `goal_contract_id`、`goal_contract_hash`；
- `run_id`、父 run/recovery attempt；
- `execution_ticket_id` 或 recovery child ticket；
- `observation_id`、`observation_hash`、来源文件哈希/更新时间；
- `goal_evaluation_id`、规则版本、criteria results；
- `diagnosis_id`、`recovery_proposal_id`、审批和审计引用。

观察、评价、诊断和提议一经写入不得原地覆盖；新一轮产生新记录并通过 lineage 指向上一轮。

## 4. 分阶段实施

### 8A：统一 Observation Model

建立 Observation schema、采集服务、来源适配器、冲突/新鲜度规则和只读 API。采集失败应返回 `incomplete/invalid` 证据，而不是虚构默认值。详细要求见[任务 8A](任务8A_统一ObservationModel.md)。

### 8B：Goal Evaluator

把自然语言目标转换为审阅时可见、可编辑、可验证的 Goal Contract；评价器逐条产出证据和缺口。FC 目标必须验证 FC artifact 存在、可重载、类型/shape/dtype 合法、范围满足且 scientific status 达标。详细要求见[任务 8B](任务8B_GoalEvaluator.md)。

### 8C：Recovery Proposal Engine

根据 Goal Gap、失败事实、Node Contract、原计划/ticket、checkpoint、风险和配额生成排序后的结构化候选。引擎只提议，不改变生命周期、不签票、不执行。详细要求见[任务 8C](任务8C_RecoveryProposalEngine.md)。

### 8D：受控 Retry 与局部 Replan

实现 Policy/Approval Gate、派生 ticket、retry/resume executor adapter、新 Reviewed Plan 分支、恢复 attempt ledger 和回评循环。详细要求见[任务 8D](任务8D_受控Retry与局部Replan.md)。

## 5. 关键决策

1. **Goal Contract 在审批前冻结**：成功条件属于计划语义，必须与 plan 一同审阅和哈希绑定。
2. **Evaluator 是确定性的**：LLM 可生成候选解释或面向用户的摘要，但不能决定 `satisfied`、风险等级或审批豁免。
3. **Observation 是事实快照，不是诊断结论**：采集层不决定恢复动作。
4. **原 ticket 不可重放**：同参数、同后端、同范围的安全重试也须签发带父票据和 attempt 的一次性 child ticket。
5. **局部 Replan 仍是新 Plan**：节点、参数、backend、输入/输出范围或目标条件任何变化都产生新 reviewed plan、plan hash 和审批上下文。
6. **Resume 与 Retry 分开**：Resume 只从已验证 checkpoint 继续既有计划未完成部分；Retry 重新执行已失败/可重试范围。
7. **fail closed**：证据缺失或冲突是 `indeterminate`，策略不明确是 `HUMAN_HANDOFF`。

## 6. 风险与缓解

| H-ID | 风险 | 强制缓解 | 验证 |
|---|---|---|---|
| H8-01 | Runtime SUCCESS 被误认为目标完成 | Goal Contract + criteria-by-criteria evaluator | FC artifact 缺失但 Runtime SUCCESS 必须 `not_satisfied` |
| H8-02 | 观察读取了错误项目或旧 run | 项目/plan/ticket/run 绑定、来源哈希、新鲜度和 path containment | 跨项目、过期、摘要漂移、路径逃逸测试 |
| H8-03 | artifact 存在但损坏或仅 metadata | 契约驱动 reload/shape/dtype/checksum/provenance 校验 | 损坏 NIfTI/NPY、空表、缺 provenance 测试 |
| H8-04 | capability/scientific 词汇漂移 | 单一枚举与显式 legacy adapter；未知值降级为 `indeterminate` | 每种旧状态映射和未知状态测试 |
| H8-05 | Recovery 提议变成隐式执行 | Proposal Engine 无执行依赖；执行仅由 8D command + gateway | 单测断言 proposal 无 runner/subprocess/file side effect |
| H8-06 | 重试复用已消费票据 | 每 attempt 签发一次性 child ticket，绑定 parent 和配额 | replay、attempt 越界、父票据不匹配测试 |
| H8-07 | 参数或 backend 偷换后沿用审批 | canonical diff；任何 reviewed contract diff 强制新 plan/approval | 参数默认值、顺序、路径 canonicalization 和 backend switch 测试 |
| H8-08 | retry 覆盖有效产物 | idempotency/overwrite contract、隔离 attempt 输出、原 run 不变 | 已存在输出、部分写、rollback/handoff 测试 |
| H8-09 | 无限循环或资源耗尽 | lifecycle/node/subject/time 四级配额；硬终止到人工接管 | 多轮失败、并发 command、重启恢复测试 |
| H8-10 | 日志泄露 PHI/凭据 | allowlisted sources、限长、结构化摘录、脱敏和默认不入模型 | credential/path/identifier redaction tests |

## 7. 验证矩阵

| 层级 | 最低证明 |
|---|---|
| Schema | v1→v2 兼容读取、不可变/hash 稳定、未知字段/枚举 fail-closed |
| Observation | 每类来源成功/缺失/损坏/过期/冲突；路径边界；重启可重载 |
| Goal Evaluation | 满足、不满足、不确定；all/any/计数/subject scope；FC 缺 artifact；metadata 不冒充 computed |
| Recovery Proposal | 七类 action、不可执行副作用、契约/风险/配额决策表、人工接管 fallback |
| Controlled Recovery | child ticket、approval policy、failed-subject retry、resume、replan、新审批、重放拒绝 |
| E2E | `execute → observe → evaluate → recover → observe → evaluate`；目标最终满足和配额耗尽两条路径 |
| Scientific | 数值 artifact reload、shape/dtype/checksum/provenance；computed/validated 真值；CPU/GPU 仅在已有能力范围验证 |
| Safety | rawdata 不写、外部后端默认关闭、未审批无 runner、审计写失败 fail-closed |

文档任务本身不要求执行测试。进入实现后，各子任务必须运行其文档列出的 focused tests；共享 lifecycle/store/runtime 变更还需运行受影响 backend suite。每次 pytest 后按 `AGENTS.md` 清理仓库根目录 `.pytest_cache/` 和 `.pytest_tmp*` 并检查 `git status --short`。

## 8. 阶段关卡

| Gate | 进入条件 | 退出条件 |
|---|---|---|
| G8-0 基线 | 阶段七 completion report、测试、工作树分类完成 | 7A–7D 契约被冻结，遗留入口清单明确 |
| G8-1 Observation | G8-0 | Observation schema/collector/adapter/API 通过完整性与安全测试 |
| G8-2 Goal Evaluation | G8-1 | Reviewed Goal Contract 生效；目标真值测试通过 |
| G8-3 Recovery Proposal | G8-2 | Proposal 无副作用；契约和配额决策覆盖七类 action |
| G8-4 Controlled Recovery | G8-3 | child ticket、审批分支、retry/resume/replan 与回评 E2E 通过 |
| G8-5 阶段完成 | G8-4 | 文档、API、状态迁移、前端（若有）和安全审查完成；无未分类执行旁路 |

## 9. 完成状态与发布移交

- [x] 阶段七当前工作树基线已通过任务 7A–7D 的 focused/integration 验证，允许在其 schema 上做显式 v2 兼容迁移。
- [x] `Goal Contract` 第一批支持 `contract_smoke`、原生完整预处理、ALFF/fALFF、ReHo、FC。
- [x] 未配置项目 recovery policy 时采用 `explicit_retry_approval`；不得以审批补足缺失或耗尽的硬配额。
- [x] 阶段八先稳定 backend API 和只读状态，不在本轮修改前端。
- [x] 旧 `SUCCEEDED` 仅兼容读取并标记 `needs_review`；没有可复核 Goal Evaluation 时不得迁移为 `GOAL_SATISFIED`。

8A–8D 已在 `17e3ebac` 实施，Observation、Goal Evaluation、Recovery Proposal、Recovery Approval/Attempt、child ticket、retry/resume/replan 与回评链路均已有代码和回归测试。`52f183bc` 基线上的阶段七/八联合验证结果为 `103 passed, 1 skipped`；跳过项是本机缺少 Windows 符号链接权限。

该结果只证明源码级契约，不证明打包应用真实多受试者恢复。阶段八因此移交阶段九完成 Windows packaged E2E、退出/强制终止/重启、失败被试隔离、局部重试和 rawdata 不变性验证。

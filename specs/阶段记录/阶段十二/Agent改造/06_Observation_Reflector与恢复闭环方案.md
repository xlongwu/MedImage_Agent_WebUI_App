# 06：Observation、Reflector 与恢复闭环方案

> 状态：Draft，待人工 Review。
> 依赖：02 事件唤醒、03 Action handler、05 计划版本。

## 1. 目标

把当前已存在的 Observation、Goal Evaluation、结果摘要和 Recovery Proposal 接回 Harness。运行结束后系统应自动形成真实结果说明；目标未满足时自动形成恢复建议，但任何重新执行或 scope 变化仍需审批。

本文中的 Reflector 指“根据结构化运行证据决定下一步”的只读步骤，不是让模型自行评判科学结果。

非目标：不替换 `GoalEvaluator`，不让模型修改 Observation/outcome，不自动审批 recovery，不提升 scientific capability level。

## 2. 当前实现分析

- `AgentTaskReconciler.reconcile_once()` 已按 `RUNNING -> observe -> evaluate_goal -> propose_recovery` 有界推进。
- `ObservationCollector.collect()` 绑定 project、lifecycle、Reviewed Plan、ticket、run 和 artifact。
- `GoalEvaluator.evaluate()` 根据 goal contract 和 Observation 确定 `satisfied/not_satisfied/insufficient_evidence`。
- `AgentTaskResultSummaryService.build()` 只有在注册、可重载数值证据支持时才输出 succeeded。
- Harness 的 `explain_result` 当前直接结束，`propose_recovery` 在生产构造时通常没有 callback；执行后不会统一唤醒 Harness。

## 3. 总体修改思路

保持 deterministic 服务先运行，Harness 后读取结果：

```text
run terminal
-> ObservationCollector
-> GoalEvaluator
-> RecoveryProposalEngine（需要时）
-> Harness wake
-> explain_result 或展示 recovery
```

Reflector 只能选择“解释、补读证据、展示已生成恢复、转人工”，不能改变 evaluation。

## 4. 详细实施方案

### 4.1 终态协调事件

修改 `AgentTaskReconciler.reconcile_once()`：完成 deterministic transitions 后发送一次 `run_reconciled` wake，包含 lifecycle ID、run ID、observation hash、evaluation hash 和 recovery proposal hash（如有）。

只有命令/monitor owner 发送；GET/read model 不发送。相同 hash 的 wake 幂等。

### 4.2 Reflector 输入与输出

输入仅包含：

- `ObservationSummary` 和完整记录引用；
- `GoalEvaluationSummary` 和 criterion 结果引用；
- `AgentTaskResultSummary`；
- Recovery diagnosis/proposal 摘要；
- 当前预算、计划版本和安全限制。

输出使用 03 的 Action：

- `explain_result`：目标满足、取消或 handoff 的说明；
- `read_evidence`：仅在明确缺少允许证据时补读一次；
- `propose_recovery`：仅在 `DIAGNOSING` 且尚无 proposal；
- `finish`：结果已解释或必须人工接管。

### 4.3 结果解释

新增或扩展 `AgentTaskResultSummaryService` 产出 `AgentResultExplanation`：

| 字段 | 来源 |
|---|---|
| `outcome`、subject counts | deterministic result summary |
| `artifact_refs`、reload status | Observation |
| `criteria` | Goal Evaluation |
| `limitations` | Observation completeness/scientific flags |
| `recommended_action` | recovery/handoff 状态 |
| `generated_text` | 可选模型解释，不参与 outcome |

若模型解释与结构化字段冲突，丢弃生成文本并记录 `AGENT_EXPLANATION_CONFLICT`，仍展示 deterministic summary。

### 4.4 恢复建议

- `AgentOrchestrator.propose_recovery()` 继续负责 diagnosis、quota usage 和 candidate 生成；
- Harness 只请求该服务或解释其结果；
- candidate 改变 node/backend/params/path/overwrite 时进入新审批；
- 低风险 retry 也保持当前显式 recovery approval，不因 Agent 自动化取消；
- 同类 recovery 超过 `max_recovery_attempts` 或无 eligible candidate 时转 `HUMAN_HANDOFF`。

### 4.5 证据不足和冲突

| 情况 | 处理 |
|---|---|
| terminal evidence 不完整/冲突 | 保持当前 `HUMAN_HANDOFF`，不自动补写成功 |
| Observation 缺失或绑定错误 | 停止 Reflector，记录领域错误 |
| evaluation `insufficient_evidence` | handoff，不让模型改为 satisfied |
| artifact 不可重载 | 结果为 partial/failed，进入 recovery/handoff |
| 模型 provider 不可用 | 使用 deterministic result summary，仍可完成任务投影 |

## 5. 数据结构变化

| 结构/字段 | 变化 |
|---|---|
| `AgentResultExplanation` | 新增结构化解释和生成文本分离 |
| `AgentHarnessStep.observation_ref` | 绑定实际 Observation/hash |
| `AgentHarnessStep.evaluation_ref` | 绑定 Goal Evaluation/hash |
| `AgentHarnessAttempt.recovery_attempts_used` | 计入恢复预算 |
| lifecycle event details | 增加 Harness wake/result explanation 引用 |

不复制 Observation、Evaluation 或 Recovery 正文到 Harness 表。
实施时全仓确认当前消费者已使用 `observation_id/observation_summary` 后，删除 `AgentLifecycleRecord.observation`、`legacy_observation_needs_review` 和对应 v1 compatibility validator，不保留双读或迁移 fallback。

## 6. 文件修改清单

| 文件 | 修改内容 |
|---|---|
| `services/agent_task_reconciler.py` | deterministic 完成后的幂等 wake |
| `services/agent_harness_service.py` | Reflector Action 推进 |
| `services/agent_orchestrator.py` | 暴露稳定结果/恢复引用，不改变权威算法 |
| `services/agent_task_result_summary.py` | 结构化解释和冲突保护 |
| `schemas/agent_harness.py`、`schemas/agent_task.py` | explanation/引用字段 |
| `schemas/agent_lifecycle.py` | 删除 legacy observation 兼容字段，保留唯一 Observation 引用 |
| `services/agent_task_read_model.py` | 结果、限制和恢复投影 |
| `tests/unit/test_observation_collector.py`、`test_goal_evaluator.py` | 绑定和真实性回归 |
| `tests/unit/test_agent_task_reconciler.py` | wake 次序和幂等 |

## 7. 风险与处理

| ID | 风险 | 处理 | 测试 |
|---|---|---|---|
| H06-01 | 模型覆盖真实 outcome | deterministic 字段只读，冲突文本丢弃 | fake success explanation |
| H06-02 | 重复 reconcile/recovery | command/hash 幂等 | 两次 terminal event 仅一 proposal |
| H06-03 | 自动恢复越过审批 | recovery 只 proposal，执行仍走 approve_recovery | gateway spy=0 |
| H06-04 | 证据绑定错项目/run | 全链 ID/hash 校验 | cross-project/stale observation |
| H06-05 | provider 故障阻止结果 | deterministic summary 是 fallback | provider down 仍可读结果 |
| H06-06 | 恢复循环 | recovery 次数和 wall time 硬上限 | repeated failure handoff |

## 8. 测试与验收

覆盖 success、partial、failed、insufficient evidence、conflict、provider unavailable、recovery approval、重复终态和重启。

```powershell
python -m pytest tests/unit/test_agent_task_reconciler.py tests/unit/test_observation_collector.py tests/unit/test_goal_evaluator.py tests/unit/test_agent_task_result_summary.py tests/unit/test_recovery_execution.py tests/integration/test_agent_harness_lifecycle.py --tb=short --basetemp=.pytest_tmp
```

人工验收：审批一次后，成功 run 自动显示结果和证据；失败 run 自动显示 diagnosis/recovery，但点击审批前不会重新执行。

## 9. 实施顺序

1. 固定现有 reconcile/Observation/evaluation 顺序；
2. 定义 explanation 和 trace refs；
3. 接入 terminal wake；
4. 实现 explain/recovery handler；
5. 增加预算与 handoff；
6. 更新 read model/前端；
7. 运行科学真实性和 recovery 回归；
8. 更新运行时、安全和能力文档。

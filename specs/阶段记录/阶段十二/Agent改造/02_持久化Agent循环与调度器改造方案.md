# 02：持久化 Agent 循环与调度器改造方案

> 状态：Draft，待人工 Review。
> 依赖：01 基线完成；03 的 Action handler 可按接口并行准备，但共享文件由单一 owner 修改。

## 1. 目标

让 Harness 在一次 create、answer 或运行终态事件后，自动连续处理多个安全步骤，直到真正需要用户、审批或人工接管。循环必须有持久状态、硬预算、lease 和重启恢复，不能变成进程内无限循环。

非目标：不执行 pipeline，不替换 `AgentTaskReconciler` 的 run monitor，不引入 Celery/Redis，不允许多个 owner 同时推进同一 lifecycle。

## 2. 当前实现分析

- `AgentHarnessService.run_one()` 已实现 attempt claim、step 幂等、模型调用、Action 校验和 lease 释放；应保留为最小执行单元。
- `AgentTaskCommandService._harness_or_plan()` 在 create/answer 后只调用一次 `run_one()`。
- `AgentHarnessScheduler.recover_once_on_startup()` 只在应用启动时扫描，且每个 lifecycle 只跑一步。
- `AgentTaskReconciler.start_bounded_monitor()` 已提供“单 owner + 有界后台检查”的项目模式，可复用其所有权思路，但不能共用 run 状态。

## 3. 总体修改思路

**选择：** 在 `AgentHarnessService` 增加 `run_until_blocked()`，内部反复调用现有 `run_one()`；`AgentHarnessScheduler` 只负责唤醒、排队和生命周期管理。

**原因：** 单步事务和多步调度职责清楚，HTTP 命令、启动恢复和后台事件可复用同一循环。

**不采用：** 不新增第二个 Agent orchestrator，不把循环写进 Route，不用一个长 HTTP 请求等待 run 完成。

## 4. 详细实施方案

### 4.1 增加有限循环

修改 `AgentHarnessService`：

```text
run_until_blocked(lifecycle, actor, wake_reason, lease_owner) -> HarnessLoopResult
```

执行流程：

1. 读取最新 lifecycle 和 attempt；
2. 判断 lifecycle/attempt 是否允许推进；
3. 调用 `run_one()`；
4. 重新从 store 读取状态，不信任上一步内存对象；
5. 若状态为 `READY` 且仍有预算，继续下一步；
6. 遇到等待、终态、错误或本次 wakeup 上限时返回。

`run_until_blocked()` 不持有跨 step 数据库事务。每一步完成后必须释放 lease，避免崩溃时锁住整个 attempt。

### 4.2 定义停止条件

| 类别 | 条件 | Loop 结果 |
|---|---|---|
| 用户输入 | `WAITING_FOR_INPUT`、`WAITING_FOR_SCIENCE_DECISION` | `waiting_for_user` |
| 执行审批 | `WAITING_FOR_APPROVAL`、`WAITING_FOR_RECOVERY_APPROVAL` | `waiting_for_approval` |
| 长任务 | `RUNNING`、`RETRYING`、`RECOVERING` | `waiting_for_runtime` |
| 正常结束 | `GOAL_SATISFIED`、`SUCCEEDED`、plan-only 完成 | `finished` |
| 人工处理 | `HUMAN_HANDOFF` | `handoff` |
| 取消 | `CANCELED` | `canceled` |
| 预算/安全 | budget、lease takeover、schema 或 capability 拒绝 | `stopped` |
| 公平调度 | 达到 `max_steps_per_wakeup` | attempt 保持 `READY`，结果为 `yielded` |

### 4.3 改造唤醒入口

修改 `AgentHarnessScheduler`，提供：

- `wake(project_id, lifecycle_id, reason)`：幂等登记唤醒；
- `run_pending_batch()`：每次处理固定数量 lifecycle；
- `recover_once_on_startup()`：只把可恢复 attempt 登记为 pending，再调用相同 batch 逻辑；
- `shutdown()`：停止接收新 claim，等待当前单步结束，不强杀未知线程。

首期使用应用 lifespan 内的单 owner 和现有 SQLite，不引入外部队列。`wake()` 只写受管状态或放入有界内存提示；真正是否需要执行必须再次查询 SQLite。

### 4.4 事件触发点

| 事件 | 触发位置 | 原因 |
|---|---|---|
| create | `AgentTaskCommandService.create()` | 开始规划 |
| answer | `answer()` | 决定已补充，可继续规划 |
| run terminal | `AgentTaskReconciler.reconcile_once()` 的命令路径 | Observation/评估后继续解释或恢复 |
| recovery approved/executed | `approve_recovery()` | 等待新 run 或处理恢复结果 |
| startup | `main.py` lifespan | 恢复 `READY` 和过期 `RUNNING` |

GET/list 路由不得调用 `wake()`。如果 `reconcile_once()` 未来可从读路径调用，必须把“状态协调”和“唤醒”限制在命令/monitor owner 内。

### 4.5 幂等、lease 和并发

- step key 继续使用 `attempt_id:step_no:input_hash`；
- wake key 使用 `lifecycle_id + lifecycle.updated_at + wake_reason` 或等价稳定版本；
- 同一 lifecycle 只允许一个有效 lease owner；
- 过期 lease 接管继续受 `MAX_LEASE_TAKEOVERS` 限制；
- 迟到 step 只能在 expected attempt status、step_no 和 context hash 同时匹配时提交；
- cancel 后 scheduler 不再 claim，新到的迟到结果被拒绝。

### 4.6 fallback 行为

provider 不可用时允许转到当前确定性 Planner，但必须：

1. 写入 `fallback_from`、`fallback_to` 和结构化原因；
2. 停止当前模型 attempt，不在同一 attempt 混用两条路径；
3. 确定性计划重新生成自己的 plan/context hash；
4. 前端明确显示实际路径；
5. 已有 Approval Summary 不受 fallback 后的新计划复用。

## 5. 数据结构变化

| 字段/结构 | 含义 | 变化 |
|---|---|---|
| `AgentHarnessAttempt.last_wake_reason` | 最近唤醒来源 | 新增 |
| `AgentHarnessAttempt.last_progress_at` | 最近完成有效 step 的时间 | 新增 |
| `AgentHarnessAttempt.yield_count` | 因公平调度让出的次数 | 新增 |
| `HarnessLoopResult` | outcome、steps_run、attempt、lifecycle、reason | 新增，service 内部结果 |
| `max_steps_per_wakeup` | 单次唤醒最多步骤 | 新配置，建议默认 3、硬上限 6 |

不新增顶层用户状态。scheduler pending 状态如需持久化，优先记录为 Harness event/attempt 字段，不新建通用任务表。

## 6. 文件修改清单

| 文件 | 修改内容 |
|---|---|
| `services/agent_harness_service.py` | 新增 `run_until_blocked()` 和循环结果 |
| `runtime/agent_harness_scheduler.py` | wake、batch、startup recovery、shutdown |
| `services/agent_task_command_service.py` | create/answer/recovery 命令触发循环 |
| `services/agent_task_reconciler.py` | 命令/monitor 终态后触发 Harness |
| `schemas/agent_harness.py` | attempt 唤醒和进展字段 |
| `services/mock_store.py`、`api/dependencies.py` | expected step/attempt 更新和必要查询 |
| `core/config_schema.py`、`.env.example` | `MAX_STEPS_PER_WAKEUP` |
| `main.py` | lifespan 启动和关闭 scheduler |

## 7. 风险与处理

| ID | 风险 | 处理 | 验证 |
|---|---|---|---|
| H02-01 | 无限循环 | step/call/proposal/time/wakeup 五层上限 | 重复 `read_evidence` fixture |
| H02-02 | 双 owner | SQLite expected status + lease owner + step_no | 并发 claim 测试 |
| H02-03 | 重启重复模型调用 | 持久 step key，先查再调用 | crash-after-step 测试 |
| H02-04 | GET 产生副作用 | wake 只在命令/monitor | spy GET/list 测试 |
| H02-05 | 单任务占满 worker | `max_steps_per_wakeup` 后 yield | 两任务公平性测试 |
| H02-06 | cancel 后迟到提交 | lifecycle terminal 和 fencing 校验 | cancel/race 测试 |

## 8. 测试与验收

新增/修改测试：

1. 三个连续安全 Action 在一次 wakeup 内完成；
2. 第四步超过上限时 yield，下一批继续；
3. 用户决定、审批、RUNNING、终态均正确停止；
4. 双 scheduler 只产生一个 accepted step；
5. 崩溃后从最后完整 step 恢复；
6. provider fallback 可见且不复用旧 hash；
7. GET/list 完全不调用 scheduler；
8. shutdown 后不再 claim，当前 step 有界结束。

执行：

```powershell
python -m pytest tests/unit/test_agent_harness_service.py tests/unit/test_agent_harness_lease.py tests/unit/test_agent_task_reconciler.py tests/integration/test_agent_harness_lifecycle.py --tb=short --basetemp=.pytest_tmp
```

人工验收：一个无需补充输入的规划任务创建后自动到达 `WAITING_FOR_APPROVAL`，页面刷新和服务重启不产生重复 step 或计划。

## 9. 实施顺序

1. 扩展 config、attempt 和 store；
2. 为 `run_one()` 补并发/崩溃 characterization；
3. 实现 `run_until_blocked()`；
4. 改造 scheduler 和 lifespan；
5. 接入 create/answer/terminal/recovery 事件；
6. 补 fallback、cancel 和公平性测试；
7. 更新 Harness 运行合同。

## 10. 待确认

**待确认：** 当前应用是否已有可直接承载 Harness wake 的统一后台 task queue。代码中现有 Agent monitor 是进程内有界线程，推荐首期沿用该模式；若发现已有 durable queue，再在实现前比较，不并存两套调度器。

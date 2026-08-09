# 12：Agent 安全审批与端到端自动化边界方案

> 状态：Draft，待人工 Review。
> 依赖：02—11。本文是这些方案进入实现前的统一安全 Gate。

## 1. 目标

明确 Agent 哪些动作可以自动完成、哪些必须等待当前用户审批、哪些永久禁止，并把规则落实到 capability catalog、计划 identity、Approval Summary、Execution Ticket、Gateway 和测试。

非目标：不新增审批系统，不降低当前 recovery approval，不允许历史审批、Memory、Skill、模型消息或多 Agent 建议成为权限。

## 2. 当前实现分析

- `AgentTaskCommandService.approve()` 已保持 summary/hash 校验 -> 科学前提 -> 审批后 dry-run -> reviewed execution。
- `ApprovalSummaryService` 绑定 project、Reviewed Plan、plan hash、MemoryContext、goal contract、node/backend、write roots、限制和 confirmations。
- `ExecutionGateway`、runner 和 `path_safety.py` 继续负责唯一执行入口、allowlist 和项目路径边界。
- `agent_capability_catalog.py` 把六种 Harness Action 标为只读并限制 state。
- Recovery 使用单独 proposal、approval、quota 和 child execution 路径；当前规则要求显式审批。

## 3. 自动化等级

| 等级 | 动作 | 是否需要本次确认 | 实现位置 |
|---|---|---|---|
| A0 | 读取 project/config/catalog/plan/run/artifact/Observation 摘要 | 否 | Evidence/Context allowlist |
| A1 | 写 attempt/context/step/trace、生成决定/解释/恢复建议 | 否 | Harness/store，受管状态 |
| A2 | 执行已批准 ticket 精确 scope、monitor、observe/evaluate | 一次执行审批后无需逐步确认 | 现有 reviewed execution/reconciler |
| A3 | 改科学参数、backend、node、input/output roots、overwrite、恢复执行 | 必须新 summary 和审批 | Planner/Approval/Recovery |
| A4 | rawdata 写入、任意 shell/Python、Gateway bypass、临床结论 | 永久禁止 | capability/path/execution boundaries |

`finish`、Skill、Memory 或模型置信度不能改变等级。

## 4. 详细实施方案

### 4.1 扩展现有 capability catalog

在 `AgentCapability` 增加：

- `automation_level`；
- `allowed_states`；
- `allowed_context_sections`；
- `allowed_output_types`；
- `requires_current_approval`；
- `side_effect_class`（`read_only`/`managed_state`）。

所有 Harness Action 只能是 A0/A1。A2 不作为模型 Action 暴露，而是用户批准后由现有 deterministic command service 自动推进。

### 4.2 审批 identity

Approval Summary/plan identity 必须绑定：

- goal/goal contract；
- EvidenceSnapshot 和 science answers；
- normalized plan、node/backend/params/dependencies；
- input/output roots 和 overwrite；
- MemoryContext、Skill、Prompt/provider 等实际影响计划的输入 hash；
- project ID、Reviewed Plan ID、有效期。

任何字段变化都在 dry-run 前拒绝旧审批。用户身份继续由 Electron approval token/后端 principal 提供，不能接受请求 body 自报 approver。

### 4.3 审批后自动推进

批准只授权当前 Execution Ticket 的精确 scope：

1. 重建并验证 summary；
2. 校验 actor、expiry、project 和 plan；
3. 运行绑定审批的 dry-run；
4. 创建/消费一次性 ticket；
5. Gateway dispatch；
6. monitor、Observation、Goal Evaluation 自动完成；
7. 任何 scope 变化停止并回到 A3。

Harness 不接触 approval token、ticket secret 或 dispatch callable。

### 4.4 路径和数据

- 所有写路径 resolve 后必须位于批准 project/write roots；
- `rawdata/`、源 BIDS/DICOM/NIfTI 永远只读；
- 显式 output_dir 不能替换并丢失默认 work/logs/reports/derivatives roots；
- Evidence/Context/Trace 只暴露 `project://`、artifact ID 或受控相对 ref；
- Agent 不能通过 payload 传入新路径；路径只能来自 Reviewed Plan schema 并经过既有检查。

### 4.5 重新审批矩阵

| 变化 | 处理 |
|---|---|
| 仅解释文案、UI 展示 | 不重新审批，不改变 plan hash |
| 补读同一 Snapshot 的只读证据 | 不审批；若改变 planning inputs 则重新生成计划 |
| science answer/goal/Memory/Skill 影响计划 | 新 plan + summary |
| node/backend/params/dependency | 新 plan + summary |
| input/output root、overwrite | 新 plan + summary |
| 同 ticket 内 monitor/observe/evaluate | 不重复审批 |
| retry/recovery | 保持 recovery approval；改变 contract 时新 plan approval |
| provider fallback 产生不同 plan | 新 identity，不复用旧审批 |

### 4.6 威胁处理

| 威胁 | 必须结果 |
|---|---|
| 模型声称“用户已批准” | 当普通不可信文本，不能改变状态 |
| Action payload 含 shell/path/URL | schema/capability 拒绝 |
| 跨项目 evidence/artifact ID | 404/结构化 scope mismatch |
| 旧 context/plan/result 迟到 | fencing/hash 拒绝 |
| prompt injection 要求泄露数据 | Context allowlist，输出 schema 拒绝 |
| UI 重复提交 approve | command idempotency/ticket replay 拒绝 |
| Memory 建议 overwrite/backend | 仍生成决定和新审批 |
| Worker/Coordinator 推荐放宽安全 | 只能报告，不能修改 policy |

## 5. 文件修改清单

| 文件 | 修改内容 |
|---|---|
| `runtime/agent_capability_catalog.py` | A0/A1 policy 字段和 fail-closed 检查 |
| `schemas/agent_harness.py` | 禁止执行/path/credential 字段 |
| `services/agent_harness_service.py` | 校验调用顺序和安全错误 |
| `services/approval_summary_service.py` | 完整 planning inputs identity |
| `services/agent_task_command_service.py` | 重新审批和批准后顺序 |
| `runtime/execution_gateway.py`、`path_safety.py` | 原则上只补测试；非必要不重构 |
| `api/agent_task_authorization.py` | 保持后端 approver 权威 |
| `docs/安全与审批/安全边界.md` | A0-A4 和 Agent 自动化边界 |

## 6. 风险与处理

| ID | 风险 | 处理 | 测试 |
|---|---|---|---|
| H12-01 | Harness 出现第二执行路径 | 依赖图和 spy 证明不可达 | execution boundary suite |
| H12-02 | 历史审批复用 | plan/planning inputs/expiry 全绑定 | tamper/replay tests |
| H12-03 | 自动化减少必要审批 | A3 矩阵强制新 summary | science/path/backend matrix |
| H12-04 | 路径逃逸/rawdata 写入 | resolve + project/write root checks | traversal/symlink/case tests |
| H12-05 | 结果被模型提升 | evaluator/artifact 权威 | metadata_only fake success |
| H12-06 | 身份来自请求 body | approval principal dependency | forged actor test |

## 7. 测试与验收

```powershell
python -m pytest tests/unit/test_agent_harness_execution_boundary.py tests/unit/test_agent_harness_capabilities.py tests/unit/test_agent_task_commands.py tests/unit/test_approval_summary.py tests/unit/test_path_safety.py tests/unit/test_execution_ticket.py tests/unit/test_capability_enforcement.py --tb=short --basetemp=.pytest_tmp
```

必须用 spy/有序日志证明：审批前无 dry-run/ticket/gateway/runner；审批后顺序正确；任何计划/scope 变化使旧审批失效；rawdata 和项目外写入在 runner 前拒绝。

人工验收：用户能清楚知道系统正在自动做什么、为什么当前需要确认、确认授权的精确范围，以及哪些动作即使用户要求也不会执行。

## 8. 实施顺序

1. 固定当前审批和路径 characterization；
2. 将 A0-A4 写入 capability contract；
3. 扩展 plan/summary identity；
4. 接入 Action/Context/Trace 安全校验；
5. 补重新审批和威胁矩阵；
6. 运行执行、路径、recovery 和科学真实性回归；
7. 更新安全文档和能力矩阵；
8. 安全 Review 通过后才允许评估默认开启 Harness。

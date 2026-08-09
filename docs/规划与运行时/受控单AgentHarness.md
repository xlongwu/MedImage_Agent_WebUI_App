# 受控单 Agent Harness

Harness 是可选的单 Agent 控制层，默认由
`MEDIMAGE_AGENT_HARNESS_ENABLED=false` 关闭。它只协助 Agent Task 的规划、
澄清、解释和恢复建议；不执行计算，也不拥有审批权限。

当前可验证的源码和 focused-test 基线见
`specs/阶段记录/阶段十二/Agent改造/01_当前Agent基线与差距分析.md`。该基线不构成
Windows packaged smoke 或正式 release 证据；未定位到该类 Harness 专属证据时，其状态为
`unknown`。

## 运行合同

- 一个 `AgentLifecycleRecord` 最多对应一个项目绑定的 Harness attempt；attempt
  和 context/step 是从属审计记录，不能成为第二个用户状态机。
- 每次 lease claim 最多处理一个 step。`run_until_blocked()` 只组合已持久化的
  单步，并在每一步后重新读取 lifecycle 和 attempt；它不持有跨 step 事务。step
  idempotency key 为 `attempt_id:step_no:input_hash`，重复 key 绝不触发第二次模型调用。
- 固定上限：6 次模型调用、8 个 action proposal、300 秒 wall time、2 次 stale
  lease takeover；每次 wake 默认最多 3 步，硬上限为 6。达到 wake 上限时 attempt
  保持 `READY`、递增 `yield_count` 并在 FIFO 队列尾部重新排队，不能独占 worker。
- Scheduler 只处理 create、answer、recovery 执行、run terminal 和 startup recovery
  的 wake 请求。它在应用 lifespan 内保持单一后台 owner；`GET`/list/read projection
  不会登记 wake、claim lease 或调用模型。startup 只恢复 `READY` 或 lease 已过期的
  `RUNNING` attempt；shutdown 拒绝新 claim，并只等待 scheduler 自己当前的有限 step。
- 唯一模型协议是 schema version 1 的 `ActionEnvelope`。允许 kind 仅为
  `read_evidence`、`request_decision`、`draft_plan`、`explain_result`、
  `propose_recovery`、`finish`；所有其他 kind 默认拒绝。
- context 由唯一 builder 产生，最大 32 KiB；只允许目标、生命周期状态、已确认
  答案、项目证据摘要、reviewed-plan/ticket 摘要和许可的 MemoryContext 字段。
  超限时先移除旧 trace，再移除可重新读取的项目详情和非必要解释。

## 安全和恢复

`draft_plan` 唯一可调用的业务服务是现有 Goal Planning Service，仍由既有
validator、Reviewed Plan 和 Approval Summary 流程决定后续状态。Harness 从不
调用 dry-run、Execution Ticket、Execution Gateway、node runner、shell 或文件
系统写入。取消和 lifecycle 终态会停止 attempt。关闭 Harness 时，Agent Task 明确
选择确定性命令路径。已启用 Harness 的 provider 不可用时，当前 model attempt 会先以
`AGENT_HARNESS_PROVIDER_UNAVAILABLE` 停止，并写入 `fallback_from`、`fallback_to`
和结构化原因；随后才由确定性 Goal Planning Service 重新生成其自身的 plan/context
hash。该 fallback 不复用旧 Approval Summary；schema、安全或预算拒绝仍只安全停止。

前端只显示后端只读 `harness_summary`（预算、状态、下一步、让出次数、实际 fallback
路径、停止原因和最新脱敏步骤摘要），不推断执行成功，也不会从 GET 触发模型调用。

## 终态 Observation 与结果说明

- terminal run 先由确定性 Reconciler 依次持久化 Observation、Goal Evaluation，必要时
  生成 Recovery Proposal；之后才发送 `run_reconciled` wake。wake 以 lifecycle、run、
  observation、evaluation 和 recovery proposal 的 hash 组成指纹，同一指纹只处理一次。
- Reflector 只能读取这些结构化记录并选择解释、补读允许证据、请求既有 recovery service
  或转人工；不能改写 evaluation、outcome、capability、artifact 或审批范围。
- `explain_result` 重新从绑定的 Observation 和 Goal Evaluation 计算 outcome、受试者计数、
  artifact/reload 状态、criterion 和限制。模型生成文本与这些字段分离；冲突文本以
  `AGENT_EXPLANATION_CONFLICT` 丢弃，确定性摘要仍可读取。
- 每个 Harness step 仅保存 Observation、Evaluation、Recovery Proposal 和 explanation 的
  引用/hash，不复制其正文；wake 和 explanation 都写入 lifecycle 审计事件。Recovery 仍只
  是 proposal，所有 retry、重规划或 scope 变化仍要经过新的显式审批。

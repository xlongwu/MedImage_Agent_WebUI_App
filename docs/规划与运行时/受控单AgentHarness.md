# 受控单 Agent Harness

Harness 是可选的单 Agent 控制层，默认由
`MEDIMAGE_AGENT_HARNESS_ENABLED=false` 关闭。它只协助 Agent Task 的规划、
澄清、解释和恢复建议；不执行计算，也不拥有审批权限。

## 运行合同

- 一个 `AgentLifecycleRecord` 最多对应一个项目绑定的 Harness attempt；attempt
  和 context/step 是从属审计记录，不能成为第二个用户状态机。
- 每次 lease claim 最多处理一个 step。step idempotency key 为
  `attempt_id:step_no:input_hash`，重复 key 绝不触发第二次模型调用。
- 固定上限：6 次模型调用、8 个 action proposal、300 秒 wall time、2 次 stale
  lease takeover。任一上限或错误会以结构化停止原因结束。
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
系统写入。取消和 lifecycle 终态会停止 attempt；应用 lifespan 只做一次有限的
ready/stale lease 恢复扫描。未配置 provider 或 Harness 出错时，Agent Task 回退
到原有确定性命令路径。

前端只显示后端只读 `harness_summary`（预算、状态、下一步、停止原因和最新
脱敏步骤摘要），不推断执行成功，也不会从 GET 触发模型调用。

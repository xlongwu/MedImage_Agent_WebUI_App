# 阶段十一：自动 AC-PC 前联合定位与受控 Agent Harness

> 状态：**计划 02 的源码实现、生产入口和 focused 基线已确认；当前 Harness 的 packaged smoke / 正式 release 证据仍为 unknown。计划 01 的独立科学参考验证仍未完成。**
> 更新日期：2026-08-22。工程结论以当前源码、完整后端回归和前端检查为依据；自动 AC-PC 前联合定位仍保持 `computed`，不得标为 `validated`。

本阶段已按当前基线完成差异实施：计划 01 只保留自动 AC-PC 前联合定位工程实现，其中 `estimated_ac_mm` 是主结果、PC 是坐标方向参考；计划 02 收敛受控单 Agent Harness 的失败语义和前端投影；计划 03 完成 Node Contract、Planner provenance、dispatch 证据链和 Memory 运营闭环。原始顺序仅作历史审计，不得据此恢复兼容入口或第二条执行路径。

1. [计划 01：自动 AC-PC 前联合定位](计划01_自动ACPC定位.md)
2. [计划 02：受控单 Agent Harness](计划02_受控单AgentHarness.md)
3. [计划 03：规划、执行、工具调用与记忆完善](计划03_规划执行工具调用与记忆完善.md)

## 当前处置

| 文件 | 当前处置 | 后续动作 |
|---|---|---|
| 计划 01 | 转为工程实施记录。自动 AC-PC 前联合定位已保持为 `computed`。 | 独立人工标注参考验证仍是单独的 Scientific Validation 任务；未完成前不得升级能力等级。 |
| 计划 02 | Harness 的源码实现、生产入口、attempt/step/context、租约、单步调度、投影与默认关闭配置已由 2026-08-09 focused baseline 复核。 | 保持单一权威路径；provider 故障结构化停止，不 fallback；不将该源码/测试事实表述为 packaged 或 release 验收。 |
| 计划 03 | Phase 0-4 差异实施与工程验收完成。 | 保持 NodeContract 权威、不可变 dispatch 证据和 Memory 三态语义。 |

计划 03 的后续实施不得与其他任务并行改动 `agent_task_command_service.py`、`mock_store.py`、`agent_task.py` 或 `PROJECT_STATE.md`，除非已经明确划分唯一所有者和合并顺序。

## 阶段共同边界

- 不写入、重命名或删除 `rawdata/`、源 BIDS、源 DICOM 或已登记源 NIfTI。
- LLM 只能提出结构化建议；只有既有的 Approval Gate、Execution Ticket 和 Execution Gateway 能启动科学计算。
- 每项新增能力默认关闭，先有单一权威合同、测试和文档，再允许在隔离项目中启用；不得以兼容层、fallback 或旧格式迁移同时保留两种行为。
- 阶段完成后，把长期规则迁入正式规范、架构与安全文档；本目录只保留阶段计划和验收记录。

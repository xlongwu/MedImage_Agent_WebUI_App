# 07：Agent 上下文分层与缓存优化方案

> 状态：Implemented，已完成代码与回归验证；仍按项目流程接受人工 Review。
> 依赖：04 EvidenceSnapshot、05 计划版本、06 Observation/Recovery 引用。

## 1. 目标

让每一步只获得完成当前 Action 必需的结构化信息，并通过稳定 hash 复用未变化的只读上下文。缓存用于减少重复组装和模型输入，不成为正确性依赖。

非目标：不注入完整 transcript、影像、日志或全部 Memory；不缓存审批、执行权限或动态 run 状态；不新增 Redis 等外部依赖。

## 2. 当前实现分析

- `HarnessContextBuilder` 是唯一 builder，已有 32 KiB 上限、allowlist、秘密/影像/日志键过滤和 deterministic truncation。
- 当前 `allowed_fields_json` 是平面 dict，包含 goal、state、answers、少量 metadata、plan/ticket/run ID 和 Memory 摘要。
- Context 已按 `context_hash` 持久化，store 提供 `get_agent_harness_context()`，但 builder 没有分区版本和复用规则。
- 最新 Observation、Goal Evaluation、Action result、预算、Prompt/Skill 版本不在上下文中。

## 3. 总体修改思路

把 context 改为固定顺序的 typed sections，每个 section 独立 hash。构建时先生成安全分区，再组合总 hash；相同总 hash 直接复用已持久化 context。任何动态分区变化都使旧缓存失效。

## 4. Context v2

| 分区 | 内容 | 来源 | 裁剪优先级 |
|---|---|---|---|
| `goal` | goal、contract、revision | lifecycle/Reviewed Plan | 保留 |
| `policy` | Action allowlist、安全版本、只读边界 | 代码/config | 保留 |
| `project_evidence` | 04 Snapshot 摘要和 refs | Evidence Service | 中 |
| `decision_state` | batch/answers 摘要 | lifecycle | 保留阻塞项 |
| `plan_state` | 当前修订、hash、node/backend 摘要 | Reviewed Plan | 高 |
| `execution_state` | ticket/run 状态摘要，不含凭据 | lifecycle/run projection | 高 |
| `latest_observation` | Observation/Evaluation/Recovery 摘要 | 06 | 高 |
| `last_action_result` | 上一步结果和 refs | Harness Step | 保留 |
| `memory_context` | 允许的 constraints/suggestions/refs | Memory Domain | 先裁剪 |
| `budget` | 剩余 step/call/time/recovery | attempt | 保留 |

每个分区包含 `schema_version`、`source_refs`、`source_hash`。总 context 记录 `section_hashes`、`omitted_fields`、Prompt/Skill 版本和 redaction policy version。

## 5. 详细实施方案

### 5.1 Builder 输入

将 `HarnessContextBuilder.build(lifecycle, project)` 改为显式输入 `HarnessContextSources`，由 service 传入 EvidenceSnapshot、plan、Observation、last step 和 budget。Builder 不自行扩张数据库查询，方便测试每个来源。

### 5.2 裁剪规则

1. 删除旧 trace 和已被新结果替代的说明；
2. Memory 只留命中的 typed refs/suggestions；
3. project evidence 只留当前 Action 需要的类型；
4. artifact 列表只留计数、失败项和所需 refs；
5. 仍超限时保留 goal、state、policy、last result、budget，写入 `nonessential_context`；
6. 必需分区仍超限时停止模型调用，返回 `AGENT_CONTEXT_LIMIT_EXCEEDED`。

不得通过截断字符串造成无效 ID/hash；引用只能整项保留或整项移除。

### 5.3 缓存规则

- Context 缓存：`context_hash` 完全相同则复用现有 immutable row；
- Evidence 缓存：绑定 project state/source hashes，任一来源变化失效；
- Prompt 前缀：固定 schema、policy、Skill 顺序，提高 provider cache 命中，但 cache miss 不影响结果；
- Step 结果：仅由 02 的 idempotency key 复用，不做模糊语义缓存；
- 不缓存 Approval Summary 验证、Execution Ticket、run terminal 或 current config health。

### 5.4 脱敏

继续使用字段 allowlist，并补充：

- 受试者标识按项目允许的逻辑 ID 展示，不发送 DICOM PHI；
- 绝对路径转换为 `project://` 或 artifact ref；
- provider credential 永不进入 context；
- 模型原始响应、完整 traceback/stdout/stderr 不进入下一步；
- MemoryContext 只通过现有 typed schema，不读取 Markdown projection。

## 6. 数据结构变化

| 字段 | 变化 |
|---|---|
| `AgentHarnessContext.schema_version` | 1 -> 2，单一新格式 |
| `sections` | 替换 `allowed_fields_json` 平面结构 |
| `section_hashes` | 新增每个分区 hash |
| `policy_version`、`redaction_policy_version` | 新增 |
| `prompt_template_version`、`skill_refs` | 进入总 hash |
| `omitted_fields` | 改为 typed section/path 列表 |

同步更新全部消费者和 fixtures，不保留 v1 fallback reader。

## 7. 文件修改清单

| 文件 | 修改内容 |
|---|---|
| `schemas/agent_harness.py` | Context v2 和 section schemas |
| `services/agent_harness_context_service.py` | 显式 sources、分区、裁剪、hash、复用 |
| `services/agent_harness_service.py` | 组装 sources 和读取 last result/budget |
| `services/agent_evidence_service.py` | source hash 和定向 evidence |
| `planner/agent_model_adapter.py` | 固定顺序序列化 |
| `services/mock_store.py` | v2 context 唯一性和重载 |
| `tests/unit/test_agent_harness_context.py` | 分区、脱敏、大小和缓存 |

## 8. 风险与处理

| ID | 风险 | 处理 | 测试 |
|---|---|---|---|
| H07-01 | 旧动态状态命中缓存 | 总 hash 绑定所有动态 section | Observation/plan 变化 cache miss |
| H07-02 | 裁剪删除关键依据 | 必需分区不可删，超限停止 | 极大 context fixture |
| H07-03 | 路径/PHI 泄露 | typed refs + redaction allowlist | secret/PHI/path corpus |
| H07-04 | 缓存成为正确性依赖 | cache miss 走同一 builder | cache on/off 等价 |
| H07-05 | hash 不稳定 | canonical JSON、固定字段顺序 | 重启/字典乱序相同 hash |

## 9. 测试与验收

```powershell
python -m pytest tests/unit/test_agent_harness_context.py tests/unit/test_agent_harness_service.py tests/unit/test_memory_retrieval.py --tb=short --basetemp=.pytest_tmp
```

验收：同一状态重复构建得到同一 hash；计划、Observation、答案或策略变化后得到新 hash；任何 Context 记录都能说明来源和省略内容，且不包含秘密、绝对 rawdata 路径或大块文本。

## 10. 实施顺序

1. 定义 v2 sections 和 hash 规则；
2. 为 v1 当前行为补测试；
3. 改写 builder 和 store；
4. 接入 Evidence/Observation/last result/budget；
5. 更新 adapter 和 replay fixtures；
6. 删除 v1 消费路径；
7. 运行脱敏、稳定性和 cache 等价测试；
8. 更新 Harness/Memory 安全文档。

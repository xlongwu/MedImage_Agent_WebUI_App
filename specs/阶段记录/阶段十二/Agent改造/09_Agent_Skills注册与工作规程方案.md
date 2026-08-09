# 09：Agent Skills 注册与工作规程方案

> 状态：Draft，待人工 Review。
> 依赖：03 Action 合同、07 Context v2、08 模型记录。
> 注意：本方案中的 Product Skill 与仓库 `.agents/skills/` 开发工具无关，二者不得互相加载。

## 1. 目标

把少量重复且稳定的 Agent 工作步骤写成版本化 Skill，使规划、结果说明和恢复 Review 使用一致的输入、禁止事项和输出格式。Skill 只影响模型如何分析，不增加任何数据访问或执行权限。

非目标：不为每个 Pipeline node 创建 Skill，不允许动态生成、远程安装或用户上传 Skill，不建立 Skill 市场，不用 Skill 替代 schema、validator、Goal Evaluator 或审批。

## 2. 当前实现分析

- 当前 Harness prompt 由 `agent_model_adapter.py:build_action_prompt()` 直接拼接固定安全说明、Action schema 和 context。
- 项目没有 Product Skill registry、Skill version/hash 或加载审计。
- Tool/Node Catalog 描述科学执行节点，不适合作为模型工作规程。
- 仓库 `.agents/skills/` 是 Codex/开发 Agent 的本地指令，不属于 MedImage 应用运行时，也不应被打包为用户任务能力。

## 3. 总体修改思路

首期只增加 3 个代码内置、静态 allowlist Skill。每个 Skill 是一个只读 `SKILL.md` 加机器可读 manifest；加载器按 lifecycle state 和 Action kind 选择，内容 hash 写入 Context/Step。未知或加载失败时使用现有基础安全 prompt，不中断确定性路径。

## 4. 首期 Skill

| skill_id | 使用阶段 | 需要输入 | 输出 |
|---|---|---|---|
| `planning_evidence_review.v1` | `read_evidence` / `draft_plan` 前 | goal、EvidenceSnapshot、能力摘要 | 缺失前提、可确认事实、禁止假设 |
| `result_explanation.v1` | `explain_result` | ResultSummary、Observation、Evaluation | 结构化说明、限制、证据 refs |
| `recovery_review.v1` | `propose_recovery` | diagnosis、proposal candidates、quota | 候选比较、阻塞风险、handoff 建议 |

科学决定批次由 04 的确定性规则生成，首期不单独用 Skill 决定需要询问什么。

## 5. Skill 文件合同

每个目录包含：

```text
src/backend/app/agent_skills/<skill_id>/
├─ manifest.json
└─ SKILL.md
```

`manifest.json` 字段：

| 字段 | 含义 |
|---|---|
| `skill_id`、`version` | 稳定标识 |
| `allowed_actions` | 可使用的 Action kind |
| `allowed_states` | lifecycle state allowlist |
| `required_context_sections` | 允许读取的 Context v2 分区 |
| `output_schema_ref` | 03 中已有 schema |
| `max_bytes` | Skill 内容上限 |
| `content_hash` | manifest + Markdown 的 canonical hash |

`SKILL.md` 只包含：适用场景、输入、禁止事项、执行步骤、输出要求、失败降级和验收。不得包含 provider key、项目数据、可执行代码或远程链接加载指令。

## 6. 详细实施方案

### 6.1 静态注册

新增 `agent_skills/registry.py`，代码中显式列出允许的 3 个 `skill_id`。启动时校验：

- ID/版本唯一；
- 文件在固定包目录内；
- manifest extra forbid；
- Action/state/context 不超出 capability catalog；
- content hash 正确；
- 输出 schema 已注册。

任一内置 Skill 配置错误时该 Skill 不可用并记录结构化错误；不得扫描任意目录发现新 Skill。

### 6.2 加载与注入

`AgentSkillLoader.load(skill_id, action_kind, state, context)`：

1. 查静态 registry；
2. 校验 action/state；
3. 只选择 manifest 允许的 context sections；
4. 生成稳定的 `SkillContextRef`；
5. 将 Skill 放在 Action schema 前的固定 prompt 位置；
6. 把 skill ID/version/hash 记录到 Context 和 ModelCallRecord。

Skill 不能要求 Context Builder 提供不在 allowlist 的字段；请求完整 transcript、rawdata、ticket secret 或完整日志时加载失败。

### 6.3 失败降级

- Skill 缺失/损坏：使用基础安全 prompt，记录 `AGENT_SKILL_UNAVAILABLE`；
- Skill 输出不符合 schema：走 03 的一次 repair；
- Skill 与系统 policy 冲突：系统 policy 优先并拒绝该 Skill；
- deterministic/rule-based provider 可以记录匹配 Skill，但不需要读取自然语言正文。

### 6.4 打包

后端打包必须显式包含 `agent_skills/**/manifest.json` 和 `SKILL.md`。打包 contract 测试验证资源存在、hash 一致；源码运行和 sidecar 使用同一资源定位函数，不硬编码开发机路径。

## 7. 数据结构变化

| 结构/字段 | 变化 |
|---|---|
| `SkillManifest` | 新增 strict schema |
| `SkillContextRef` | skill ID/version/hash/sections |
| `AgentHarnessContext.skill_refs` | 新增 |
| `AgentHarnessStep.skill_refs` | 新增 |
| `ModelCallRecord.skill_hashes` | 新增 |

Skill 内容不写入 SQLite，只存版本/hash；运行时从随应用发布的只读资源加载。

## 8. 文件修改清单

| 文件 | 修改内容 |
|---|---|
| `src/backend/app/agent_skills/`（新增） | 3 个 Skill、registry、loader、schemas |
| `planner/agent_model_adapter.py` | 固定 Skill 注入位置 |
| `services/agent_harness_context_service.py` | 按 manifest 提供分区 |
| `schemas/agent_harness.py` | Skill refs |
| 后端 packaging spec/contract tests | 包含静态资源 |
| `tests/unit/test_agent_skills.py`（新增） | manifest、allowlist、hash、降级 |

## 9. 风险与处理

| ID | 风险 | 处理 | 测试 |
|---|---|---|---|
| H09-01 | Skill 获得额外权限 | capability catalog 始终先于 Skill | Skill 请求 forbidden section |
| H09-02 | 动态/远程 Skill 注入 | 静态 registry，不扫描外部目录 | unknown path/ID 拒绝 |
| H09-03 | Skill 变更未进入计划身份 | hash 写入 planning inputs | 改 Skill 后旧审批失效 |
| H09-04 | 打包缺资源 | packaging contract | packaged resource/hash test |
| H09-05 | Skill 故障阻断确定性路径 | 基础 prompt fallback | 删除 fixture 后 rule path 可用 |
| H09-06 | 与 `.agents/skills` 混淆 | 独立目录和文档声明 | loader 拒绝仓库外目录 |

## 10. 测试与验收

```powershell
python -m pytest tests/unit/test_agent_skills.py tests/unit/test_agent_harness_context.py tests/unit/test_agent_harness_capabilities.py tests/unit/test_desktop_packaging_contract.py --tb=short --basetemp=.pytest_tmp
```

验收：每个 Skill 都能说明为何存在、读取哪些分区、输出什么、失败如何降级；删除或篡改 Skill 不会扩大权限，也不会影响确定性 Planner 可用性。

## 11. 实施顺序

1. 确定首期 3 个 Skill 内容；
2. 定义 manifest/ref schema；
3. 实现静态 registry/loader；
4. 接入 Context/Prompt/ModelCallRecord；
5. 增加安全负例和示例；
6. 接入打包；
7. 更新 Harness 和安全文档。

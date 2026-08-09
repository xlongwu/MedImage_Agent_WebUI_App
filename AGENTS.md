# AGENTS.md — MedImage Agent 仓库执行规则

本文件是后续 Codex、Claude Code 及其他开发 Agent 在本仓库中的项目级执行约束。它只保留会直接影响开发正确性、安全性、科学有效性和交付质量的规则；架构细节、能力说明和当前状态分别由专项文档维护。

---

## 1. 文档用途与适用范围

### 1.1 作用域与继承

- 本文件位于仓库根目录，适用于整个仓库。
- 当前仓库没有子目录 `AGENTS.md`。以后新增子目录规则时，只能补充该目录特有约束，不得机械复制本文件。
- 子目录规则默认继承本文件；如必须覆盖上级规则，必须写明覆盖原因、适用目录和验证方式。
- 任务说明可以缩小修改范围，但不得削弱 rawdata 只读、Approval Gate、路径安全、审计、科学真实性和测试要求。

### 1.2 权威来源

不同问题使用不同事实来源：

| 问题 | 权威来源 |
|---|---|
| 项目级开发、安全、验证与文档规则 | `AGENTS.md` |
| 当前任务范围和验收标准 | 已批准的任务说明、Issue 或方案 |
| 当前真实行为 | 当前源码、测试和实际运行证据 |
| 当前版本、限制、打包和下一步 | `PROJECT_STATE.md` |
| 架构职责与调用链 | `docs/架构与决策/系统架构.md` |
| 科学能力及验证等级 | `docs/项目概览/能力矩阵.md` |
| 安全与审批说明 | `docs/安全与审批/安全边界.md` |
| 文档分类和入口 | `docs/文档索引.md` |
| 用户和开发入口 | `README.md`、`README_CN.md` |

代码通过测试不自动证明科学结论正确、文档准确或任务全部完成。来源冲突时，必须说明当前行为、治理规则要求和需要修正的差异。

### 1.3 项目边界

MedImage Agent 是面向 rs-fMRI 研究工程的确定性 Plan-then-Execute 平台，不是临床诊断产品、临床决策支持产品、通用外部命令执行器或无限自主 Agent。

- LLM 只能规划、解释、校验和提出动作。
- 实际计算只能经受控服务、Approval Gate、Execution Ticket、唯一 Execution Gateway、Pipeline Runtime 和已注册 node runner 完成。
- 外部 MATLAB/SPM/DPABI/GPU/DICOM 执行默认关闭；启用新路径必须有明确任务范围、人工批准、环境门控、审计、安全路径、失败处理和测试。
- 禁止作出诊断、治疗或临床性能声明。

---

## 2. 开发前必须完成的检查

### 2.1 确定任务模式

开始修改前必须选择能完整交付目标的最窄模式，并在最终报告中写明：

| 模式 | 适用范围 | 最低检查范围 |
|---|---|---|
| Focused Fix | 小范围 Bug、单接口、文档小修 | 目标文件、调用方、相关测试 |
| Feature Bundle | 完整用户或开发功能 | 前端到存储/运行时的完整调用链 |
| Architecture / Refactor | 拆分、替换、依赖或状态重构 | 现有行为刻画、当前消费者、共享测试 |
| Scientific Validation | ALFF/fALFF、ReHo、连接、过滤、回归、atlas、数值后端 | 请求到数值产物、注册、溯源和参考验证全链 |
| Release / Packaging | 版本、依赖、CI、sidecar、Electron、安装包 | 版本面、锁文件、构建、启动、产物清单 |
| Documentation | 规则、README、架构或流程文档 | 引用路径、命令、实现锚点和文档一致性 |

Focused Fix 不得借机广泛重构；其他模式新增修改文件必须确属完整调用链所需，并在最终报告解释。

### 2.2 必做动作

1. 完整阅读本文件、任务说明和与任务直接相关的专项文档。
2. 运行 `git status --short`，识别用户已有修改、未跟踪文件和生成物；不得假设工作区干净。
3. 阅读每个目标文件、直接调用方、数据结构和现有测试；不得在未理解现有实现时直接重写。
4. 搜索旧字段、旧路径、旧接口和重复实现，并确认所有当前消费者已改用新实现后移除废弃项。
5. 对方案驱动任务列出必做项、非目标、调用链和验收命令，并在开发过程中持续逐项对照。
6. 确认是否触及受保护区域：Pipeline Runtime、node runner、Approval Gate、Execution Gateway、转换/预处理算法、产物注册、状态迁移、路径/allowlist。
7. 修改前确认测试不会使用持久桌面数据库、用户工作区或研究数据。

### 2.3 单一所有者与现有改动

- 一个任务只能有一个实现所有者和一个一致的 diff；审查者不得静默成为第二个并行实现者。
- 必须保留无关的用户改动，不得使用 `git reset --hard`、`git checkout --` 或大范围清理来获得“干净”工作区。
- 未明确要求时，不得提交、推送、打 tag、发布或上传产物。

---

## 3. 项目级核心约束

### 3.0 架构与实现原则

- 不保留向后兼容性。需求变更时，必须同步更新当前消费者并移除废弃的路径、字段、接口、配置和实现；禁止新增兼容层、fallback、shim 或迁移逻辑来同时维持新旧行为。
- 选择能完整满足当前需求的最简单实现。禁止为尚未确认的需求预先引入抽象、配置、间接层或扩展点。
- 系统必须逐层生长：先交付端到端可用的最小版本，再在已可用的产品上叠加能力；不得以未完成的复杂性替换可工作的产品。
- 组件必须保持模块化、职责清晰，并按既有分层边界组织；不得以跨层耦合或万能模块换取短期便利。
- 优先复用项目现有依赖；当成熟且维护良好的库能降低整体复杂度或提高可靠性时，应优先采用。新增实现或依赖前，必须先核查现有依赖的文档和类型，不能仅凭假设认定其缺少所需能力；无明确理由不得重造常见功能。
- 架构决策必须面向长期维护，选择可作为最终设计持续演进的方案；禁止接受仅为暂时可用、计划后续替换的权宜实现。

### 3.1 后端分层

后端必须保持：

```text
Route -> Request/Response Schema -> Service
      -> Runtime / Runner 或 Scientific Kernel
      -> State and Artifact Storage
```

- Route 只处理 HTTP、依赖注入和错误映射；禁止在 Route 中放复杂业务、数值算法或直接外部执行。
- 新端点必须有明确 domain router，并在 `src/backend/app/main.py:create_app()` 注册；不得继续扩张无关的遗留聚合路由。
- 结构化请求/响应必须使用 schema；禁止为复杂公共接口继续堆积手解析 `dict[str, Any]`。
- Route 的 catch-all 必须经 `src/backend/app/api/_errors.py:raise_api_error()` 保留结构化领域错误。
- 新读接口必须通过 `ProjectStore` Protocol 和 `FastAPI Depends()` 隔离存储，不得新增对全局 `mock_store` 的直接耦合。
- 运行时 JSON 状态必须使用 `atomic_write_json()`，并包含 `_schema_version`；禁止用 `Path.write_text(json.dumps(...))` 写受管状态。
- 配置经 `ConfigService` 读取，环境变量使用 `MEDIMAGE_` 前缀；替换 accessor 时必须同步更新调用方并删除旧 accessor。

### 3.2 Runtime、Node Registry 与执行边界

- Pipeline Runtime 是确定性执行唯一来源；服务编排 kernel，runner 调用共享实现，禁止在 Route/Service 复制数值算法或建立旁路执行。
- 新 node 放在 `src/backend/app/runtime/node_registry_plugins/` 的正确插件中并暴露 `REGISTRY`；稳定 `node_id` 不得改名，重复 ID 必须失败。
- 新可执行 node 必须同步 Tool Catalog、Approval Gate、审计、安全 allowlist、API/前端能力展示和测试。
- 受保护模块只有在任务明确要求、已有行为被测试刻画、当前消费者影响已评估并运行安全回归时才可修改。

### 3.3 前端边界

- 前端只能通过共享 HTTP API client 和批准的 Electron bridge 与后端交互；禁止直接使用文件系统或执行外部工具。
- Domain API wrapper 位于 `src/frontend/src/lib/api/`，共享类型位于相应 type 模块；不得重新建立根级单体 API 文件。
- 新业务功能放入 `features/`，复杂状态进入 domain hook/controller；`App.tsx` 只保留应用壳和编排职责。
- 后端状态和安全 gate 始终权威；UI 不得用本地乐观状态推断执行成功、能力等级或审批结果。
- 完整功能必须表示 loading、empty、disabled、success、partial 和 failure 状态中适用的部分。
- 用户可见文案必须走 i18n catalog；错误码、ID、hash 等机器标识保持不翻译，由前端映射。

### 3.4 API、状态和持久格式变更

任何 API、schema、状态或持久格式变更必须同时检查并按需更新：

- 后端 schema、route、service 和 contract tests；
- 前端 API wrapper、TypeScript 类型、调用方、i18n 和测试；
- 同步更新所有当前消费者，并删除旧字段、旧路径和旧接口；
- 持久状态保留 `_schema_version`，但格式切换必须采用单一权威格式并验证重启恢复；禁止保留旧格式读取、迁移或 fallback；
- README、API/架构/状态文档。

禁止把 contract 变化作为顺手清理；禁止只修改生产者而遗漏消费者，也不得以兼容层掩盖未更新的消费者。

### 3.5 数据、路径和安全不变量

- 用户 DICOM、BIDS、NIfTI、`rawdata/` 和已登记源数据永远只读；不得删除、覆盖、重命名或作为输出根。
- 所有写路径必须 resolve 后位于已批准项目边界和明确 write roots（如 `work`、`logs`、`reports`、`derivatives`、`exports`）内。
- 显式 node 输出目录不得意外覆盖或抹掉其他默认批准 write roots。
- 禁止硬编码凭据、私有绝对路径和研究数据路径；`.env` 不得提交，配置示例维护在 `.env.example`。
- 禁止绕过 Approval Gate、Execution Ticket、审计、safe-path 或 allowlist。
- 禁止从 LLM 文本直接执行命令、引入无限自主循环或把历史审批当作当前权限。

### 3.6 科学计算真实性

记忆系统还必须保持以下不变量：独立 memory SQLite 是长期记忆唯一权威源，
desktop SQLite 只提供项目 consent、事务 outbox、来源投影和 forget ledger；
Markdown/JSON 只能是可重建投影。安装级与项目级生成/使用门控默认关闭，记忆
必须项目隔离并保留来源、版本、状态和审计。记忆不得成为执行权限、审批、能力
等级、科学有效性或当前环境真值；科学参数只能作为建议，必须在当前任务重新
确认，并将实际 `MemoryContext` hash/引用绑定到 Reviewed Plan 和 Approval
Summary。忘记必须清除明文、保留最小 tombstone 并阻止旧来源自动重建。

能力状态必须使用真实含义：

| 等级 | 含义 |
|---|---|
| `unavailable` | 没有可执行实现 |
| `scaffolded` | 仅接口或占位，不执行有效计算 |
| `metadata_only` | 只生成计划/元数据，没有声明的数值产物 |
| `computed` | 数值产物真实生成、可重载并已注册 |
| `validated` | 通过定义明确的数值和独立参考验证 |

- 禁止将计划、shape、路径、占位文件或元数据标成 `computed`、`completed` 或 `validated`。
- 科学功能必须覆盖参数校验、kernel、数值产物、原子持久化、注册、溯源、状态和重载验证。
- 数值算法只能有一个 canonical kernel；新增前必须搜索已有实现。
- 产物必须存在、可重开、shape/dtype 正确、关联输入与参数，并处理部分写失败。
- 简化、预览或子集处理必须明确标注并记录 subset rule；不得冒充完整标准算法。
- 多后端必须显式选择，定义 CPU/GPU 的支持边界和容差，并验证实际后端；不得隐式切换后端。

### 3.7 依赖、版本和可复现性

- 禁止依赖版本写成 `latest`；前端 manifest 与 lockfile 必须一起更新。
- 删除依赖前必须检查可选执行路径和打包；重依赖保持 optional，除非任务明确改变安装要求。
- 实现通用能力前必须优先检查并复用项目已有依赖；需要新增依赖时，优先选择成熟、维护良好且能降低整体复杂度或提高可靠性的库，并核查其文档和类型。
- 稳定文档不得写维护者私有 Python/Node/Conda/MATLAB/CUDA 绝对路径。
- 随机科学操作必须接收或记录确定性 seed。
- 应用版本唯一来源是 `src/backend/app/version.py` 中的 `APP_VERSION`；版本变更必须是显式 Release 任务，并同步 `pyproject.toml`、前端、Electron、README 和当前发布文档。
- 历史发布记录绑定历史版本，不得改成当前版本。

---

## 4. 功能开发与修改规则

### 4.1 新功能

- 必须实现完整调用链，不得只完成可见 UI、单一路由或占位产物。
- 必须覆盖成功、失败、空状态、禁用状态、不安全输入和重启恢复中适用的场景。
- 新接口、配置、环境变量、依赖、命令、目录、状态和能力必须在同一任务内完成测试与文档同步。

### 4.2 Bug 修复

- 必须定位根因和最小影响面，先补能复现问题的回归测试，再修复生产代码（纯文档错误除外）。
- 不得只改错误文案、吞异常、降低断言或放宽容差来“通过测试”。
- 修复后必须搜索同类路径和调用方；具有复发可能的问题必须补入本文件或适当子目录 `AGENTS.md`。

### 4.3 重构

- 移动代码前必须用 characterization tests 记录公共行为。
- 必须保留当前需求仍要求的 API、执行、状态、产物和科学语义；有意变化必须同步更新所有当前消费者并删除被替换实现，不得增加迁移或兼容路径。
- 禁止把无关产品功能混入架构重构；禁止为规避核心 bug 在外围复制第二套逻辑。

### 4.4 删除和废弃

- 删除前必须检查 Git tracking、动态注册、可选依赖、打包资源、fixtures、文档和当前入口。
- 必须全仓搜索旧路径、旧字段、旧命令和旧标识；更新全部当前引用后，必须一并删除废弃实现、入口和文档，不得保留 shim、fallback 或迁移逻辑。
- 名称像 `memory/`、`outputs/` 或 `dist/` 不代表整个目录可删除；tracked fixture/source 必须保留。

---

## 5. 历史问题与防复发规则

本节只记录已在代码、测试或本项目会话中确认、且可能复发的问题。新增近似问题时优先扩充现有规则。

### 5.1 目标路由不得混淆“不支持”与“缺少前提”

- **触发条件**：修改 Agent Task 目标分类、中文/英文意图、BIDS 前提或工作流选择。
- **必须执行**：先识别核心意图，再独立检查项目证据；支持目标缺输入时进入可恢复的输入补充，真正不支持时进入目标更新。计划限定语和“生成 QC 报告”“不修改 rawdata”等修饰语不得破坏核心路由。
- **禁止事项**：不得依赖一个完整句子、单个关键词或任意英文摘要解析；不得把所有失败归为 `UNSUPPORTED_GOAL`。
- **验证方式**：覆盖中英文正例、近似反例、缺少证据、由不支持目标更新为支持目标，以及已确认的 plan-only、头动、ALFF/fALFF、ReHo 表述。
- **相关文件**：`src/backend/app/services/goal_planning_service.py`、`src/backend/app/planner/goal_contract_builder.py`、`tests/unit/test_agent_task_commands.py`。

### 5.2 审批顺序必须以副作用为边界

- **触发条件**：修改 Reviewed Plan、Approval Summary、approve 命令、dry-run、Execution Ticket、Execution Gateway 或执行状态。
- **必须执行**：严格保持以下顺序：

  ```text
  持久化 Reviewed Plan
  -> 构建并持久化稳定 Approval Summary
  -> WAITING_FOR_APPROVAL
  -> 审批时校验未变化的 summary/hash、actor 和 scope
  -> 绑定审批后运行 dry-run
  -> dry-run 通过后创建 ticket 并经唯一 gateway dispatch
  ```

  科学执行计划在进入 dispatch 前还必须校验其输入链：例如 ReHo 必须由计划内 realignment/smoothing 产物供给，或绑定经过审阅的显式预处理 BOLD 输入；缺少前提时以结构化错误阻断审批执行。

- **禁止事项**：规划阶段不得运行执行 dry-run、runner、外部工具或 dispatch；失败的 post-approval dry-run 不得落入真实执行；不得用历史审批批准改变后的计划。
- **验证方式**：用 spy/有序调用日志证明没有审批前副作用、顺序正确、summary hash 变化被拒绝、`AGENT_DRY_RUN_BLOCKED`/`AGENT_EXECUTION_PREREQUISITE_MISSING` 或等价错误阻止 dispatch；ReHo 回归必须覆盖缺少和具备预处理输入链两种情况。
- **相关文件**：`src/backend/app/services/approval_summary_service.py`、`src/backend/app/services/agent_task_command_service.py`、`src/backend/app/services/reviewed_execution_service.py`、`src/backend/app/runtime/execution_gateway.py`、`tests/unit/test_approval_summary.py`、`tests/unit/test_agent_task_commands.py`。

### 5.3 Plan-only 必须真实“不执行”

- **触发条件**：目标包含“仅生成计划”“不执行计算”或等价语义。
- **必须执行**：持久化 Reviewed Plan、计划 hash 和证据链接；结果必须包含 `execution_performed=false` 或等价结构化字段，并清楚表示执行/数值计算已跳过。
- **禁止事项**：不得创建执行审批、dry-run 执行记录、Execution Ticket、run 或数值产物；不得把无 run 视为证据丢失。
- **验证方式**：断言审批、dry-run、ticket、gateway 和 runner 均未调用；重启后方案 ID、节点、状态和 Reviewed Plan 证据仍可读取。
- **相关文件**：`src/backend/app/services/agent_task_command_service.py`、`src/backend/app/services/agent_task_read_model.py`、`src/backend/app/services/agent_task_result_summary.py` 及其单元/API/前端测试。

### 5.4 生命周期命令与错误必须后端权威

- **触发条件**：修改 create、answer/update goal、approve、cancel、reconcile 或公共状态映射。
- **必须执行**：命令按 command ID 幂等；同一 lifecycle 只能有一个未解决决策；只允许状态机明确支持的取消；重复取消幂等；运行中或终态取消返回结构化领域拒绝。执行命令返回前必须进行一次有界终态协调；仍在运行时再启动单 owner 的有界 monitor。前端只轮询后端投影并在终态停止，不得长期保留本地“运行中”。
- **禁止事项**：不得伪造成功、用 UI 本地状态覆盖后端、把结构化拒绝渲染为通用“服务不可用”。GET 投影不得产生 reconcile 或其他副作用。
- **验证方式**：覆盖允许/拒绝/重复/终态取消、命令重放、审批返回前终态协调、后台 monitor、重启投影、等待用户/需注意/完成/取消的前端映射；GET/list 只读测试必须证明不会触发状态迁移。
- **相关文件**：`src/backend/app/services/agent_task_command_service.py`、`agent_task_read_model.py`、`agent_task_reconciler.py`、`src/frontend/src/features/agent/`、对应测试。

### 5.5 前后端结构化数据和 i18n 必须同步

- **触发条件**：修改 Agent Task schema、状态、计数、证据链接、结果摘要或用户文案。
- **必须执行**：优先增加结构化字段并同步 backend schema、client wrapper、TypeScript type、i18n catalog 和中英文测试。遗留稳定摘要若必须解析，只能使用一个集中 parser。Runs 工作区必须合并后台任务记录与项目级 `/runs` 记录；Agent Task 创建的 project run 以 `run_id` 为标识并覆盖同 ID 的陈旧后台记录，不得因为旧 `/api/tasks` 列表为空而显示总数 0。
- **禁止事项**：不得在多个组件用正则解析任意英文来推断计数、路径、能力或安全状态；不得把后端英文摘要直接当作唯一 UI 状态。
- **验证方式**：API contract、client test、Agent workspace/controller test、project-run 映射/去重测试，以及 `en`/`zh-CN` 两种呈现。
- **相关文件**：`src/backend/app/schemas/agent_task.py`、`src/frontend/src/lib/api/agentTasks.ts`、`src/frontend/src/lib/types/agentTask.ts`、`src/frontend/src/i18n/messages/`、`src/frontend/src/features/agent/`。

### 5.6 写入范围必须完整且 rawdata 只读

- **触发条件**：修改审批 scope、项目路径、显式 output_dir、artifact 注册或打包/测试工作区。
- **必须执行**：resolve 每个路径并验证在项目边界；审批范围保留配置的 `work/logs/reports/derivatives` 等根；项目托管的 `data/` 可保存数据索引等运行证据并作为只读产物发现根，但源 BIDS/NIfTI 和 rawdata 只能作为输入。转换或衍生输出必须是项目内独立目标。
- **禁止事项**：不得因一个 node 给出显式输出而丢弃默认根；不得将项目根、系统目录、源数据或未解析变量设为写根。
- **验证方式**：覆盖目录遍历、项目外路径、rawdata 输出/产物拒绝、project `data/` 证据接受、显式输出与默认根并存、路径大小写/符号链接等平台适用场景。
- **相关文件**：`src/backend/app/runtime/path_safety.py`、Tool Catalog/Approval Summary、相关 service 和路径测试。

### 5.7 能力和完成状态必须反映真实产物

- **触发条件**：新增/修改 ALFF/fALFF、ReHo、预处理、QC、报告、GPU 或外部工具能力。
- **必须执行**：检查从请求到可重载数值产物的完整路径；记录算法、参数、输入标识、版本、backend、dtype、checksum、warning 和输出注册。
- **禁止事项**：不得把 stub、plan、contract、metadata 或空报告标成计算完成；不得仅凭 route/service 测试声称算法 validated。
- **验证方式**：kernel 单测、边界输入、产物重载、溯源、golden/独立参考，以及多 backend 容差测试（如适用）。
- **相关文件**：科学 kernel、runner、artifact service、`docs/项目概览/能力矩阵.md` 和科学测试。

### 5.8 桌面启动和打包结论必须分级

- **触发条件**：修改 frontend build、sidecar、Electron、PyInstaller、启动健康检查或发行物。
- **必须执行**：区分 build success、sidecar health、packaged launch、renderer smoke、人工 GUI workflow 和真实科学执行。packaged smoke 使用隔离 workspace/userData，并验证 sidecar ready、frontend index、React root、main landmark、无 renderer error、正常退出后 sidecar 停止；修改生命周期时还必须验证重复启动只保留一个 owner，以及主进程异常退出后 sidecar 自动停止。任何构建探测脚本都必须在返回前终止其完整 sidecar 进程树并确认构建 EXE 不再被占用。
- **禁止事项**：不得把构建成功称为 GUI 验证；不得复用用户数据库/工作区；不得把 unpacked EXE 单独当作可携带应用，它依赖同目录 `resources/` 和 Electron 文件。
- **验证方式**：`npm --prefix desktop/electron run check`，按任务运行 Windows 打包脚本及隔离 packaged smoke，并在报告逐层说明证据。
- **相关文件**：`desktop/electron/`、`desktop/packaging/`、`tests/unit/test_desktop_packaging_contract.py`、`docs/桌面与前端/桌面应用打包.md`。

### 5.9 打包产物只保留任务要求的最新表面

- **触发条件**：日常 Windows 验证或 release 打包。
- **必须执行**：日常验证覆盖 canonical `desktop/electron/dist/win-unpacked/`；只有显式 Release 任务才生成 installer、portable、签名或版本化产物。先完成验证并核对 sidecar payload，再清理重复/中间产物。
- **禁止事项**：不得每次源码修改都新增带时间戳或后缀的 EXE；不得在 smoke 前删除构建输入；不得删除 dependency cache、tracked 资源或用户状态来“清理”。
- **验证方式**：清点 `desktop/electron/dist/` 和 `desktop/packaging/`，确认只保留请求的最新应用表面，并验证保留包可启动。
- **相关文件**：`desktop/packaging/build_all_windows.ps1`、`build_backend.ps1`、`build_desktop.ps1`、Electron dist。

### 5.10 Windows 进程、端口和锁定目录必须按所有权处理

- **触发条件**：启动脚本、sidecar 健康超时、端口冲突、PyInstaller `_MEI*` 或打包目录被锁。
- **必须执行**：终止进程前验证 PID、可执行路径和归属；递归删除前解析绝对路径、检查 Git tracking 并确认目标是预期生成目录。锁定残留必须报告准确路径和进程证据。
- **禁止事项**：不得杀死未知端口占用进程；不得扩大 ACL、take ownership、删除父目录或使用宽泛通配符绕过锁。现有 `start.bat`/`start.sh` 的端口清理行为不得复制到新脚本，修改时必须先增加进程归属校验。
- **验证方式**：清理后复查进程、端口、目录和 `git status --short`；无法安全删除时保留并报告，不得伪称清理完成。
- **相关文件**：`start.bat`、`start.sh`、`desktop/packaging/`、Electron main process。

---

## 6. 测试与验证要求

### 6.1 后端命令

使用当前激活环境的 `python`，除非任务明确指定环境：

```powershell
python -m pytest --collect-only -q --basetemp=.pytest_tmp
python -m pytest <focused-test-paths> --tb=short --basetemp=.pytest_tmp
python -m pytest --tb=short --basetemp=.pytest_tmp
```

每次 pytest（成功、失败、中断或超时均包括）结束后必须：

1. 保存退出码和输出；
2. 确认 pytest 进程已退出；
3. 只清理仓库根目录直接子项 `.pytest_cache/` 和 `.pytest_tmp*`；
4. 删除前验证解析后的绝对路径、直接子项关系和名称；
5. 确认零匹配残留并运行 `git status --short`。

不得借此清理通用 tmp、`__pycache__`、fixture、用户数据或仓库外路径；清理失败不得掩盖测试失败。

### 6.2 前端和桌面命令

前端源码或配置变更至少运行：

```powershell
npm --prefix src/frontend run format:check
npm --prefix src/frontend run typecheck
npm --prefix src/frontend run test
npm --prefix src/frontend run build
```

共享 project-runs 行为变化时增加 `npm --prefix src/frontend run test:project-runs`。Lint 相关变更运行 `npm --prefix src/frontend run lint`；必须如实说明 CI 中 lint 是否为非阻塞，不能把未执行的检查写成通过。

桌面主进程、preload 或 packaging contract 变更至少运行：

```powershell
npm --prefix desktop/electron run check
```

Release / Packaging 任务再按 `docs/桌面与前端/桌面应用打包.md` 运行 sidecar、unpacked、packaged smoke、版本一致性和产物清单；构建与 GUI 工作流验证必须分别报告。

### 6.3 按变更类型的最小矩阵

| 变更 | 必须验证 |
|---|---|
| 文档 | Markdown 结构、真实路径、真实命令、链接/引用、重复与冲突；有配置的文档检查则运行 |
| Focused Fix | 复现测试 + focused 回归；共享基础设施变化时扩大范围 |
| API/schema | 后端 contract/API + 前端 client/type/caller + 当前消费者切换与废弃项删除 |
| Feature Bundle | backend focused + frontend + success/failure/empty/unsafe + 关键状态迁移 |
| Architecture/Refactor | characterization + 受影响层完整套件 + 当前 API/状态消费者更新 |
| Scientific | kernel、边界、产物 reload、provenance、golden/reference、backend 等价（适用时） |
| Agent Task | routing、create/update/approve/cancel/read/result、审批顺序、plan-only 零执行、重启投影、中英文前端 |
| Release/Packaging | backend、frontend、sidecar、launcher/Electron、packaged smoke、版本和产物清单 |

### 6.4 测试隔离和失败报告

- 测试必须用 dependency override、`monkeypatch`、临时 `SQLiteDesktopStore` 或临时工作区；禁止写持久桌面数据库。
- CI 是持续验证权威；本地 Windows 结果不得外推到其他 OS、MATLAB/SPM、GPU 或真实数据。
- 不得隐藏失败、删除/弱化测试或标记 xfail 只为获得通过。
- 最终报告必须给出精确命令、结果、失败是否由本变更引起、环境限制和未验证区域。

---

## 7. 文档持续同步机制

### 7.1 每个任务都必须做文档影响检查

任何新功能、功能修改、重构、Bug 修复、API/数据/状态/配置/依赖/测试策略变化，在完成前必须检查：

- 是否新增或改变模块、目录、命令、环境变量、依赖、接口、字段、状态或用户流程；
- README、专项文档和示例是否仍准确；
- 本文件现有规则是否仍适用，是否出现可复发的新风险；
- 旧命令、旧路径、旧字段、旧接口和旧能力声明是否残留；
- 测试说明、验证命令和 CI 描述是否需要调整。

代码通过测试不能替代文档影响检查。

### 7.2 变更与文档映射

| 变更类型 | 必须检查并按需更新 |
|---|---|
| 用户使用方式、安装、启动 | `README.md`、`README_CN.md`、相应使用/桌面文档 |
| 架构、模块边界、目录 | `docs/架构与决策/系统架构.md`、`docs/文档索引.md` |
| API、schema、状态、持久格式 | API/生命周期/架构文档、切换说明、`PROJECT_STATE.md`（当前状态确有变化时） |
| 安全、审批、路径、执行能力 | `AGENTS.md`、`docs/安全与审批/`、能力矩阵 |
| 科学算法、backend、产物、验证等级 | `docs/项目概览/能力矩阵.md`、算法/验证文档、README 状态说明 |
| 配置、环境变量、依赖 | `.env.example`、README、配置/打包文档、manifest/lockfile |
| CI、测试策略和命令 | `AGENTS.md`、`docs/开发与测试/开发工作流.md`、CI 配置 |
| 打包、版本、发行物 | `PROJECT_STATE.md`、打包文档、README、发布记录（只写当前版本） |

### 7.3 新功能、修改和重构

- 新功能必须在任务完成前同步受影响文档，不得把文档留给后续任务。
- 修改/重构必须查找并移除或标注旧描述；目录或命令变更必须验证所有文档引用。
- 详细实现放入专项文档；`AGENTS.md` 只保留长期规则和链接。

### 7.4 Bug 和测试问题

- 修复后必须判断是否可能复发。
- 可能复发时，必须把根因转成可执行规则、补自动化回归并更新相应文档；禁止只记录错误日志或现象。
- 如果判断无需更新 `AGENTS.md`，最终报告必须说明原因，例如问题是一次性数据、外部环境或已有规则已充分覆盖。

### 7.5 维护本文件

- 优先修改、合并或删除已有规则，不得不断追加近似条款。
- 删除失效规则和临时状态；不得写精确测试通过数量、个人环境路径、单次任务流水账或技术报告。
- 每条规则必须能指导动作或验证；无法从代码、测试、日志、文档或已确认会话证据验证的内容只能标为待核实，不得写成强制事实。
- 每次任务最终报告必须列出：已更新文档、检查后无需更新的文档、`AGENTS.md` 是否更新；未更新时给出具体原因。

### 7.6 稳定文档职责

- `PROJECT_STATE.md` 是当前验证状态快照，不是开发日记，不得追加每次修复和测试计数。
- 例行 Completion Report 放在最终回复、PR 或提交信息，不为每个小修创建 Markdown 报告。
- 只有阶段/里程碑级架构结果才可按约定保留在 `specs/阶段记录/`。
- 临时任务文件不得无限累积；耐久信息完成归档后应按任务要求移除。

---

## 8. 完成任务前检查清单（Definition of Done）

只有以下项目全部满足，任务才可声明完成：

- [ ] 已声明任务模式、目标、范围和非目标。
- [ ] 已对照方案确认所有当前范围必做项已实现，未只接通表层。
- [ ] 关键 Route/Schema/Service/Runtime/Storage 或前端调用链已完整检查。
- [ ] rawdata、Approval Gate、路径、审计、执行和科学真实性不变量未被削弱。
- [ ] API、状态、配置、持久格式的当前调用方已同步更新，废弃路径和实现已删除。
- [ ] 必要的回归、类型、构建、科学或 packaged smoke 已运行并如实记录。
- [ ] pytest 临时目录和本任务生成物已按安全规则清理。
- [ ] 已完成文档影响检查并同步相应文档；是否更新 `AGENTS.md` 有明确结论。
- [ ] 已检查 `git diff` 和 `git status --short`，未覆盖无关用户修改，未包含秘密、用户数据或不应提交的产物。
- [ ] 未发现重复代码、调试残留、虚假成功、临时路径或未解释风险。

最终 Completion Report 必须包含：

1. **Task**：任务模式、交付目标、分支/工作区（适用时）；
2. **Files changed**：逐个分类为 modified/created/restored/deleted 并说明原因；
3. **Behavior delivered**：之前行为、现在行为、边界和失败状态；
4. **API / Scientific impact**：contract、切换、能力等级、产物与溯源影响；
5. **Validation**：精确命令、结果、环境和未验证区域；
6. **Documentation impact**：已更新、确认无需更新和 `AGENTS.md` 决策；
7. **Git / artifacts**：保留的用户改动、排除的生成物和打包清单；
8. **Remaining risks**：仅列真实阻塞、外部限制或尚未验证项；没有则明确写“无已知未完成项”。

禁止以“主要代码已完成”“测试大部分通过”或“构建成功”代替上述完成条件。

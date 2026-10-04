# AGENTS.md — MedImage Agent

本文件只保留局部代码不易推断、且改错会破坏项目边界或证据可信度的规则。当前行为以源码和测试为准，当前状态见 `PROJECT_STATE.md`。
详细入口：[系统架构](docs/架构与决策/系统架构.md)、[安全边界](docs/安全与审批/安全边界.md)、[能力矩阵](docs/项目概览/能力矩阵.md)、[记忆系统方案](docs/架构与决策/记忆系统设计方案.md)；
[开发工作流](docs/开发与测试/开发工作流.md)、[桌面应用打包](docs/桌面与前端/桌面应用打包.md)和[人工审核清单](specs/待人工审核校验清单.md)。

## 项目边界

- 本项目是 rs-fMRI 研究工程平台，不是临床诊断或治疗决策产品。
- LLM、对话历史和记忆只提供建议；禁止从模型文本直接执行命令或绕过审批，Agent 不得自动批准恢复或无限循环。
- 用户 `rawdata/`、原始 BIDS/NIfTI 和已登记源数据只读。

## 架构与状态

- 所有实际执行只能走 Reviewed Plan、Approval Gate、Execution Ticket、唯一 Execution Gateway、Pipeline Runtime 和已注册 node runner；不得增加旁路。
- 新增或修改可执行 node 时，使用正确 registry plugin 和唯一权威 Node Contract，并保持 `node_id` 稳定。
- 同步检查该 node 的 Tool Catalog、审批、审计、allowlist/write roots、能力展示和测试；科学算法保持单一 canonical kernel，route/service 不复制数值实现。
- Agent Task 先持久化 Reviewed Plan 和稳定 Approval Summary，再等待人工批准。批准后复核 plan/summary hash、actor、scope 和绑定环境，再运行 dry-run；通过后才创建 ticket 并 dispatch。
- dry-run 失败必须停止在 ticket/dispatch 之前；科学执行还须校验计划绑定的输入与前置产物。
- `plan-only` 只保存 Reviewed Plan、plan hash 和证据链接；不得创建执行审批、dry-run、ticket、run 或数值产物。
- lifecycle 命令由后端状态机裁定并按 command ID 幂等；每个 lifecycle 至多一个未解决决策，只允许合法状态取消。GET/list 投影只读，恢复不得重复结果未知的调用。
- API/schema/state contract 变更须同批更新所有当前消费者和测试，删除过时字段或路径，不保留双轨、fallback 或 shim。受管 JSON 状态原子写入并带 `_schema_version`；格式变更须验证重启恢复。
- 前端只通过共享 HTTP API client 和批准的 Electron bridge 访问后端；后端状态与审批结果权威。生命周期文案使用语义 code、类型化参数和 i18n，不得解析英文摘要或用本地乐观状态推断结果。

## 安全、科学与证据

- 写入路径 resolve 后必须位于明确批准的项目输出根目录；显式 node 输出不得意外剔除其他批准写根。路径与 allowlist 细节见[安全边界](docs/安全与审批/安全边界.md)。
- Windows 子进程须保持 suspended，直到确认实际 token 是零 capability AppContainer 且已绑定 Job Object；校验失败必须 fail closed，不得移除隔离属性。
- 表示 plan、approval、ticket 或 provider 身份的 hash，必须来自实际绑定 payload 或当前实现字节；不能对常量或版本标签取 hash 冒充身份。
- `computed` 表示数值产物已写入、登记且可重载；`validated` 还需独立参考验证。简化、preview、subset 和 backend 限制必须保留在结果与声明中，backend 不得静默切换；各能力以[能力矩阵](docs/项目概览/能力矩阵.md)为准。
- Project Memory 默认关闭、按项目隔离且仅作建议。独立 memory SQLite 是记忆权威源；desktop SQLite 只保存 consent、事务 outbox、来源投影和 forget ledger。
- 当前 MemoryContext 的来源与 hash 必须绑定 Reviewed Plan/Approval Summary；记忆不授予执行权限或科学有效性，forget 必须阻止旧来源重建。细节见[记忆系统方案](docs/架构与决策/记忆系统设计方案.md)。
- `rule_based`、mock 和故障注入只证明离线合同/安全边界；模型效果须有获准的真实 provider 评测。基线只比较 manifest、案例和条件匹配的报告；执行、恢复、科学成功分别需要真实 lifecycle、run 和可重载产物证据。
- `specs/待人工审核校验清单.md` 中标为人工责任的授权、判读和验收只能由相应人工关闭；Agent 自评或机制获批不等于验收通过。

## 验证

- 按[开发工作流](docs/开发与测试/开发工作流.md)选择受影响层的 focused checks；测试使用隔离 store/workspace 和合成输入，不得写用户数据库或研究数据。
- pytest 使用 `--basetemp=.pytest_tmp`。确认进程退出后，只清理由本次测试生成、resolve 后位于仓库根直接子项且未被 Git 跟踪的 `.pytest_cache/`、`.pytest_tmp*` 和中间产物。
- 不得宽泛删除、强制接管 ACL/所有权或终止未知进程。进程池测试不得依赖跨 Windows spawn 传播的进程内 monkeypatch。
- Release/Packaging 分别报告 build、sidecar、packaged launch、renderer smoke、可见 GUI 和科学验证；一层通过不能替代其他层。

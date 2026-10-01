# 阶段十 Agent-first 验收记录

记录日期：2026-07-16

## 验收边界

本记录覆盖阶段十的源代码实现、自动化测试和交互合同。按照维护者本次指示，
exact-SHA 打包、Electron 可见界面真实数据流程、安装器和发布版本对齐暂不执行，
后续统一处理。因此本记录不能作为 packaged GUI、安装包或发布候选的通过证据。

## 源代码身份

- 基线 HEAD：`cbe434e53b7b1140b6f21145b4391e22f0bd797c`
- 分支：`codex/phase10-agent-first-ui`
- 状态：dirty feature worktree；阶段十改动尚未提交，因此不存在可声明的 exact-SHA 候选
- Python：3.13.9
- Node：24.16.0
- npm：11.13.0
- Electron manifest：31.7.7
- PyInstaller：6.21.0

清单哈希：

| 清单 | SHA-256 |
| --- | --- |
| `pyproject.toml` | `FC648E29B2688DAB9AB959CD54B37477B3D75E02953709D09511A78F20F53B9D` |
| `src/frontend/package.json` | `9E04F0DDC9C5617E057A4E586CDEF4171B7A84BD05FBFD46EFA0C6A96C8024F7` |
| `src/frontend/package-lock.json` | `BC8CBE9CB02CEE2F8B76B6AF040D66FAF61210A8BA87CF464F77A14D760B4D06` |
| `desktop/electron/package.json` | `B87FC12C3FC00197357ADF8DBC15B699CBF23DC64E337D99E4C8D307F1852951` |
| `desktop/electron/package-lock.json` | `D20716839704CAD21676927CF131404E8889870750EB97A4026BBEA74A745D99` |

## Flow F1：BIDS/NIfTI 到 FC

源代码链路已实现：Agent Task create → 项目上下文 → 确定性 stage policy → 科学决策
暂停点 → validator/native dry-run → Reviewed Plan → 单一 Approval Summary → 既有
Ticket/Gateway。已有 native BIDS-to-FC 集成测试验证真实数值 FC/Fisher-z 矩阵可重载、
对称且输入校验和不变；Agent UI 标准路径不显示手工 dry-run、validation、report 或
refresh 操作。

源代码结论：通过。打包可见 UI 结论：未执行。

## Flow F2：DICOM 到 preprocessing 到 FC

源代码链路已实现受控 `native_dicom_conversion_execute` 节点。它必须获得 Gateway
签发的 execution context、批准的 node/backend、项目与 conversion package 绑定，
并复用现有 release-readiness/handoff 服务。partial conversion 不得成为 preprocessing
输入；旧公开 conversion execute 入口继续 fail-closed。已有原生 DICOM、安全 schema、
ticket、registry、path 和 conversion gateway 测试覆盖该边界。

源代码结论：受控 gateway 合同通过。真实 packaged visible-UI 流程和新的 exact-SHA
rawdata pre/post manifest：未执行，不声明完成。

## Flow F3：单受试者失败到一次审批恢复

源代码链路已实现 bounded reconcile：terminal evidence → observe → evaluate →
diagnose/propose 后停止，绝不自动批准。Agent 恢复命令只采用服务端推荐且 eligible 的
candidate，一次用户批准后复用现有 RecoveryExecutionService；child ticket、quota、
replay、expiry、失败受试者隔离和成功 artifact 不覆盖由既有 recovery 测试覆盖。
Reviewed execution 返回 RUNNING 后会启动同 lifecycle 唯一的有限监视任务，最多 900
次检查且总墙钟不超过 900 秒；发现 terminal evidence 后自动 observe/evaluate/propose
并停止。进程重启时仍由显式 startup reconciliation job 恢复，不从 GET 路径写状态。

源代码结论：通过。打包退出、强制终止和重启 smoke：未执行。

## 负向与重启场景

| 场景 | 源代码证据 | 结论 |
| --- | --- | --- |
| 缺 Atlas / GSR / TR 冲突 / Template / 已有 run / 实验 GPU | command science-decision matrix tests | 执行审批前停止 |
| stale/tampered Approval Summary | approval hash tests | runner 前拒绝 |
| project switch during polling | Agent controller generation/abort test | 不串任务 |
| missing/reload-failed artifact | result summary and goal evaluation tests | 不显示完成 |
| recovery replay/expiry/scope expansion | recovery and child ticket tests | runner 前拒绝或要求新 plan |
| cross-project Agent Task URL | project-scoped API/read-model tests | 404/安全错误 |
| startup reconcile | explicit env gate and single-pass test | 默认不写，启用后一次有限处理 |

## Gate 状态

| Gate | 状态 |
| --- | --- |
| F10-1 source | 通过；backend full 与 frontend typecheck/lint/format/test/build 全绿 |
| F10-2 packaged | deferred by maintainer |
| F10-3 BIDS visible packaged workflow | deferred；source layers validated |
| F10-4 DICOM visible packaged workflow | deferred；source gateway contract validated |
| F10-5 recovery | source validated；packaged restart smoke deferred |
| F10-6 truth | source result/evaluation/artifact truth tests validated |
| F10-7 docs | user guide、architecture、PROJECT_STATE、capability matrix 已同步 source 状态 |

## 源码验证记录

| 验证 | 结果 |
| --- | --- |
| `python -m pytest --collect-only -q --basetemp=.pytest_tmp` | 通过；收集 4145 项；收集期 4 项因缺少 `pydicom` 跳过 |
| `python -m pytest -q --disable-warnings --tb=short --basetemp=.pytest_tmp` | 通过；全部可运行 backend 测试通过；可选 DICOM、外部工具、GPU、Windows symlink 场景按环境跳过 |
| 每次 pytest 后限定清理 | 通过；仓库根目录 `.pytest_cache` / `.pytest_tmp*` 均为 0 |
| `python -m ruff check <本次变更 Python 文件>` | 通过 |
| `npm.cmd --prefix src/frontend run format:check` | 通过 |
| `npm.cmd --prefix src/frontend run typecheck` | 通过 |
| `npm.cmd --prefix src/frontend run lint` | 通过，0 warning |
| `npm.cmd --prefix src/frontend run test` | 45 files / 255 tests 通过 |
| `npm.cmd --prefix src/frontend run build` | 通过；Vite production build 成功 |
| `git diff --check` | 通过 |

测试过程中发现并修正了两类回归：旧前端 source test 仍假定新项目进入 Overview，
已更新为阶段十 Agent-first 默认入口；已转换 BIDS 上下文曾被错误追加 DICOM conversion
节点，现仅在存在 `agent_conversion_execution_ready=true` 的受审准备证据时追加该节点。

## 交互指标

基线和 Agent-first 的统一计数见 `阶段十_交互指标对比.json`。标准 BIDS 流程为 3 次
显式操作，DICOM 含一次必要决策时为 4 次，恢复流程为 2 次；自动加载、后台 polling
和可选技术详情不计数，并在指标文件中明确列为排除项。

## 数据与产物边界

本次验收没有提交 DICOM/BIDS/NIfTI、outputs/work/logs/reports、SQLite 数据库、截图、
录屏、打包目录或安装器。所有版本化 evidence 均不含研究数据和私人绝对路径。

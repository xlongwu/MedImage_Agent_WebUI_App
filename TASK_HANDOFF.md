# TASK_HANDOFF

更新：2026-10-04（上海时区）

## 当前状态

- 仓库位于 main，HEAD 为 c79dcaacb773ad29757a8a82f2e938dfbc161fbd；工作区包含此前已有的大量未提交修改。保留这些改动，不 reset、不提交、不推送、不打 tag、不发布，也不为获得 clean tree 删除用户文件。
- 应用版本仍为 0.6.0-rc1。0.6.0-rc2 已终止；v0.7.0-rc1 仍需 D-09 能力评审和显式 Release 任务。
- 当前任务已完成：保留唯一最新 Electron 包，重写中英文 README，精简并更新本文件及 PROJECT_STATE.md。
- 旧的 2026-09-12 包备份 desktop/electron/.original-dist-before-rebuild-20261004/ 已按用户最新要求删除。删除前确认它未被 Git 跟踪、路径位于仓库内且无进程使用。
- 唯一保留的 Electron 应用输出是 desktop/electron/dist/win-unpacked/。包 provenance 记录时间 2026-10-04T02:54:42Z、版本 0.6.0-rc1、HEAD c79dcaacb773ad29757a8a82f2e938dfbc161fbd、clean=false。
- desktop/packaging/build/production-launch/workspace/_MEI242642 是剩余的旧 PyInstaller 临时解包目录。当前没有发现其所属进程，但精确路径的删除被 Windows 拒绝访问。不要扩大 ACL、接管所有权或删除父目录；等待环境所有者处理。

## 最近一次已记录验证

以下来自 2026-10-04 的重打包任务，不是本次文档更新中重新运行的结果：

- 后端完整 pytest：3189 passed、31 skipped、3 warnings，exit 0；collect-only：3216 collected，exit 0。
- 前端：52 个测试文件、266 个测试通过；format、typecheck、lint、build 和 project-runs 检查通过。CI lint 为非阻塞。
- Electron：npm --prefix desktop/electron run check 通过。
- 打包聚焦回归：151 passed、1 warning，exit 0。
- 当前 canonical 包的隐藏 renderer/shell smoke 与 restricted-process packaged smoke 通过；sidecar 停止、renderer health 为 200、React root 非空、无 console errors。没有对当前包运行 restart workflow、可见 BIDS/DICOM/recovery 流程或 installer。
- 结果与逐字证据位于 artifacts/technical-audit-fixes/20261003-r02-r11/ 及 specs/阶段记录/审核计划/。上一阶段 source/evidence manifest 是历史快照，不代表当前整份 dirty tree。

本次文档与清理任务未运行 pytest、前端测试或重新构建。检查发现仓库根 .pytest_tmp 当前已不存在。

## 未完成事项

1. 普通 shell 权限可用，但此前 CUA/browser 初始化报告 node_repl kernel exited unexpectedly 和 helper_unknown_error: apply deny-read ACLs。待环境修复后，重新获取当前源码 UI 证据；不得复用旧截图或绕开批准工具。
2. D-03 仍需人工解释与 SDK 复核 Windows AppContainer 网络硬化；D-09 仍需评审 Memory Domain 及当前 API/持久化契约扩展。状态与关卡以 specs/待人工审核校验清单.md 和 PROJECT_STATE.md 为准。
3. P-01/P-04 的 packaged restart 尚未在获批的 clean exact-SHA 候选上验证。获得适用批准后，隔离 workspace、userData、数据库和 evidence directory，核对原 run/ticket/dispatch、singleton、sidecar 终止及候选输入 hash。
4. 可见 Electron GUI 工作流、独立科学参考、真实数据人工判读及 Release/installer/tag/publication 仍按人工清单处理。不得用 dirty-tree 包、源码测试或历史候选关闭这些关卡。
5. 清理 _MEI242642 仍受文件访问拒绝阻挡。仅在权限/环境可访问后重新确认路径、Git 跟踪、进程所有权和清理范围；不尝试 ACL 或 owner 变更。

## 续做约束

先读 AGENTS.md、PROJECT_STATE.md 和人工审核清单，再从上述未完成事项继续。所有修改继续在 main 上进行并保留现有 dirty diff。Pipeline Runtime、Approval Gate、Execution Gateway、node runner、科学算法和状态迁移属于受保护范围；任何 release/GUI/外部执行证据都必须绑定当前获批候选，且 rawdata 始终只读。

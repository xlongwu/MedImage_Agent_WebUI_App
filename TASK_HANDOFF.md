# TASK_HANDOFF

更新：2026-10-04（上海时区）

## 当前任务

修复 `webui-app/main` 上报告的 Windows sandbox process-tree 自检失败，以及 backend release-readiness 的两项测试失败。修复基于 `main` / `webui-app/main` 的共同基线 `8e7726afad9d1c1ab3bdc6fa0a72d4bd77f6019c`。

## 已完成

- README 中前端启动说明统一为先进入 `src/frontend` 再执行 `npm run dev`，满足就绪检查并保持中英文一致。
- `build_release_readiness()` 支持可选输出目录；release-readiness 单测通过 pytest 隔离目录写报告，避免覆盖仓库中既有的 `outputs/reports/release_readiness/`。
- 修正 `spawn_child_tree` 探针：固定使用 System32-only `PATH`，切换到已授权的 `staged_input` 目录后再启动固定 `cmd.exe` 子进程，避免 `cmd.exe /c` 的 C 运行时引号转义与当前目录权限问题。正对照使用 `max_processes=2` 并确认子进程标记可见，限制探针使用 `max_processes=1` 并确认标记不可见；没有放宽 Job Object、AppContainer 或进程数限制。
- 更新 Windows 进程树回归断言和安全边界说明。

## 验证

Windows 本机 focused 回归命令：

```powershell
python -m pytest tests/unit/test_desktop_backend_entry.py tests/unit/test_runtime_subprocess_boundary.py tests/unit/test_sandbox_process_request.py tests/unit/test_windows_process_sandbox.py tests/unit/test_sandbox_policy.py tests/unit/test_execution_environment_service.py tests/unit/test_execution_ticket.py tests/api/test_sandbox_attempt_api.py tests/integration/test_windows_process_sandbox.py tests/integration/test_windows_sandbox_process_tree.py tests/unit/test_release_readiness.py --tb=short --basetemp=.pytest_tmp_ci_repair -q
```

结果：`105 passed, 1 warning, exit 0`。单独的 `spawn_child_tree` 实际 Job Object 探针为 `1 passed`。完成后确认 pytest 已退出并清理 `.pytest_tmp*` 与 `.pytest_cache`；仓库根目录没有遗留 pytest 测试目录。警告是 FastAPI TestClient 对 httpx/Starlette 的弃用提示，与本修复无关。

完整 backend suite 和此次修复后的远端 GitHub Actions 结果尚未取得；不能据 focused suite 声称全量 CI 通过。

## 提交、打包与未完成事项

- 修改仍在 `main`；上一提交 `8e7726a` 已与 `webui-app/main` 同步。完成本次提交推送后，检查远端 main SHA 与本地 HEAD 一致，再查看新 Actions 结果。
- 本次没有重新构建 Electron 包或 installer。`desktop/electron/dist/win-unpacked/` 的既有包 provenance 来自 `c79dcaacb773ad29757a8a82f2e938dfbc161fbd`，早于本次 backend runtime 自检修改；不得把旧包的 smoke 证据当作当前候选证据。没有生成新 installer/portable。
- D-03 Windows AppContainer 网络加固人工/SDK 复核、D-09 Memory Domain/API 持久化能力评审、获批 exact-SHA packaged restart、可见 GUI 工作流和独立科学验证仍未完成，以 `PROJECT_STATE.md` 和人工审核清单为准。
- `_MEI242642` 的旧 PyInstaller 解包目录此前因 Windows 拒绝访问而无法清理；本次没有更改其 ACL/owner，也没有删除父目录。

## 续做

先确认本修复已推送，再读取对应 GitHub Actions 结果；若失败，从新日志继续定位。不要复用旧 package evidence。所有更改继续在 `main`，保留用户本地 `.zcodeignore`；rawdata 始终只读。

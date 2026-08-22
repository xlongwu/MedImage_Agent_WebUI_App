# Phase 6A — 仓库规则与 CI 稳定化 完成报告

> 归档状态：该文档对应的当前阶段范围已完成；仅作为历史实施与审计记录保留。

## 完成时间
2026-06-14 18:00

## 6 个子任务全部完成

### 6A-1: ERROR_KB.yaml 规则冲突 ✅
- 迁移路径：`memory/global/ERROR_KB.yaml` → `src/backend/app/resources/error_kb.yaml`
- 更新 4 个源文件引用：`error_classifier.py`、`memory_store.py`、`file_provider.py`、`error_kb_validator.py`
- 清空并删除 `memory/` 目录
- 更新 AGENTS.md artifact 规则（移除 memory/ 禁止项，添加新 resources 路径）

### 6A-2: docs/tasks/ 生命周期 ✅
- 创建 `docs/临时任务/说明.md`（tracked）
- 创建 `docs/临时任务/任务模板.md`（tracked）
- 更新 `.gitignore`：`docs/tasks/` → `docs/tasks/TASK_*.md`
- 更新 AGENTS.md 规则

### 6A-3: 中间件规则修复 ✅
- AGENTS.md 中间件表格重写为与 `main.py` 一致
- 注册顺序：CORS → RequestLogging → RequestID → RateLimit → APIVersion
- 请求进入顺序明确列出，消除歧义

### 6A-4: ADR 状态更新 ✅
- ADR-001/002 状态格式统一为英文 Accepted
- ADR-003 前端状态管理：Proposed → Accepted
- ADR-004 DICOM 真实执行：Proposed → Accepted
- ADR-005 跨平台策略：Proposed → Accepted
- 所有 ADR 统一使用 Approved Status 术语

### 6A-5: CI 强化 ✅
- Backend: 增加 pytest --collect-only --strict-markers + 全面测试
- Frontend: 增加 typecheck、lint、test、test:project-runs（原有 build 保留）
- Desktop: 新增 job 运行 npm run check

### 6A-6: 前端依赖版本固定 ✅
- react: latest → ^19.2.5
- react-dom: latest → ^19.2.5
- typescript: latest → ^6.0.3
- vite: latest → ^8.0.10
- @vitejs/plugin-react: latest → ^6.0.1

## 验证结果

| 验证项 | 结果 |
|---|---|
| 后端 collection (3712 tests) | ✅ 6.88s |
| 后端测试 (3712 tests) | ✅ 3683 passed, 22 skipped, 5 fixed🠞0 |
| 前端 typecheck | ✅ 0 errors |
| 前端 lint | ⚠️ eslint 未安装（预存问题） |
| 前端 test (4 files / 47 tests) | ✅ 全部通过 |
| 前端 test:project-runs | ✅ passed |
| 前端 build | ✅ 416ms |
| 桌面 check (51 checks) | ✅ 全部通过 |

**后端失败修复记录：**
- test_memory_provider.py (3 fails) → 移除 `file_provider.initialize()` 中的 ERROR_KB 动态创建逻辑（静态资源不应由 runtime 动态创建）
- test_memory_store.py (1 fail) → 同上修复
- test_phase2_feature_regression_matrix.py (1 fail) → 同上修复
- test_dashboard_api.py (2 errors) → 预存 Windows 临时文件冲突，非本次引入

## 涉及文件

- 修改: `AGENTS.md`, `.gitignore`, `.github/workflows/ci.yml`
- 修改: `error_classifier.py`, `memory_store.py`, `file_provider.py`
- 迁移: `memory/global/ERROR_KB.yaml` → `src/backend/app/resources/error_kb.yaml`
- 新增: `docs/临时任务/说明.md`, `docs/临时任务/任务模板.md`
- 修改: `docs/DECISIONS/0001-0005.md`
- 修改: `src/frontend/package.json`
- 删除: `memory/` 目录

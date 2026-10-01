# MVP Release Smoke Checklist

This checklist is for the desktop real-project MVP release hardening pass. It
validates the project creation, reviewed plan, reviewed execution, run history,
and artifact preview loop without adding new features or running external
MATLAB, SPM, DPABI, GPU, or clinical workflows.

This is the legacy real-project/run smoke surface. Current Agent Task and
packaged-release status is authoritative in `PROJECT_STATE.md`, the lifecycle
document, and the desktop packaging guide.

## Scope

- Real BIDS/rawdata project plumbing is in scope.
- Reviewed plan persistence and execute-reviewed preflight/execution plumbing
  are in scope.
- Run history, summary preview, artifact discovery, and safe artifact preview
  are in scope.
- Rawdata writes, open-ended autonomous execution, MATLAB/SPM/DPABI/GPU
  execution, NIfTI viewers, report editors, dashboards, packaging, and
  installers are out of scope.

## Internal Validation

- `contract_smoke` is included as an **internal validation node** (not
  user-visible). It does not write rawdata, does not run external tools
  (MATLAB/SPM/DPABI/GPU), and is used solely to validate the executor node
  contract.
- Test: `tests/integration/test_node_contract_smoke.py` (happy path + failure
  path).

## Required Environment

Use Python 3.11+ from the currently activated project environment:

```powershell
python --version
python -c "import sys; print(sys.executable)"
python -c "import fastapi; print(fastapi.__version__)"
python -c "import pydantic_core; print(pydantic_core.__file__)"
python -c "import numpy; print(numpy.__version__)"
```

If these imports fail, activate or repair the project environment before
continuing; do not substitute a maintainer-private interpreter path in stable
documentation.

## Backend Automated Checks

```powershell
python -m pytest tests/unit/test_project_history_plans.py tests/unit/test_project_history_runs.py tests/unit/test_run_summary_preview.py tests/unit/test_run_artifact_discovery.py tests/unit/test_run_artifact_preview.py tests/integration/test_real_project_safe_smoke.py --tb=short --basetemp=.pytest_tmp
python -m pytest --tb=short --basetemp=.pytest_tmp
```

Expected result: all focused checks pass, and full pytest passes with only
expected optional dependency or missing external BIDS skips.

All pytest commands use the repository-local `--basetemp`. Tests must isolate
state with dependency overrides, temporary `SQLiteDesktopStore` instances, and
temporary workspaces. If a failure references the persistent desktop database,
stop and fix the test isolation; never delete user desktop state to make a test
pass. After every pytest invocation, follow the validated cleanup procedure in
`AGENTS.md` and preserve the original test exit status.

## Frontend Automated Checks

Run from the repository root:

```powershell
npm --prefix src/frontend run format:check
npm --prefix src/frontend run typecheck
npm --prefix src/frontend run test
npm --prefix src/frontend run test:project-runs
npm --prefix src/frontend run build
```

If system Node/npm is unavailable, use the bundled Codex Node runtime and record
the exact command in the completion report.

## Manual Desktop Smoke

Verify each item before release:

| # | Check | Expected |
|---|-------|----------|
| 1 | Create a real project from a bounded BIDS/rawdata fixture | Project is persisted and rawdata remains referenced read-only |
| 2 | Generate `project_config.yaml` | File is inside the managed project directory |
| 3 | Generate `dataset_index.json` | File is inside the managed project data directory |
| 4 | Open Plan Review for the project | Real project context is injected before review |
| 5 | Save the reviewed plan | `reviewed_plan_id` is persisted and listed |
| 6 | Execute reviewed plan with required confirmation | A unique `run_id` is generated |
| 7 | Query run link/history | The run link is visible for the selected project |
| 8 | Open summary preview | Status, counts, warnings, outputs, and failed nodes render when present |
| 9 | Open artifact list | Run-scoped artifacts are listed with warnings when needed |
| 10 | Preview JSON, CSV, Markdown, text, and log artifacts | Bounded content preview is shown |
| 11 | Inspect binary, NIfTI, and MAT artifacts | Metadata-only detail is shown |
| 12 | Confirm rawdata protection | No rawdata file is written or modified |
| 13 | Use browser/web fallback | No direct filesystem access; path text is copyable |
| 14 | Use desktop open-path bridge | `window.medimage.openExternalPath` works when available |
| 15 | Run full backend pytest | Passes or only expected skips occur |
| 16 | Run frontend smoke/typecheck/build | All pass |

## External BIDS Smoke Gate

Run `tests/integration/test_external_bids_safe_smoke.py` only when the user
provides `MEDIMAGE_EXTERNAL_BIDS_SMOKE_DIR`.

Do not:

- Randomly scan user disks.
- Write rawdata.
- Copy large datasets.
- Run MATLAB/SPM/DPABI/GPU.
- Silently fall back to synthetic data.

## Release Stop Conditions

Stop the release if any of these occur:

- Rawdata is written or modified.
- Execution bypasses the Approval Gate.
- Artifact preview accepts arbitrary filesystem paths.
- Summary or artifact reads succeed outside project output roots.
- Binary, NIfTI, or MAT files are content-read instead of metadata-only.
- Frontend accesses the local filesystem directly instead of backend APIs or
  the desktop bridge.
- Full pytest or frontend build fails without a documented expected reason.

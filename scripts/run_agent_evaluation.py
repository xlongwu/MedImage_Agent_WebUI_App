"""Run only the fixed, data-free Agent evaluation suite."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--provider", choices=("rule_based",), default="rule_based")
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    try:
        from src.backend.app.core.config_schema import AgentModelRuntimeConfig
        from src.backend.app.planner.agent_model_adapter import DefaultAgentModelAdapter
        from src.backend.app.schemas.agent_eval import AgentEvalManifest
        from src.backend.app.services.agent_evaluation_runner import AgentEvaluationRunner

        manifest = AgentEvalManifest.model_validate_json(Path(args.manifest).read_text(encoding="utf-8"))
        report = AgentEvaluationRunner().run_manifest(
            manifest=manifest,
            model_adapter=DefaultAgentModelAdapter(config=AgentModelRuntimeConfig()),
        )
    except (OSError, ValueError) as exc:
        print(json.dumps({"error_code": type(exc).__name__}, ensure_ascii=False))
        return 2
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report.model_dump(mode="json"), ensure_ascii=False, sort_keys=True), encoding="utf-8")
    print(json.dumps({"case_count": report.case_count, "gate_passed": report.gate_passed, "report": str(output)}, ensure_ascii=False))
    return 0 if report.gate_passed else 1


if __name__ == "__main__":
    raise SystemExit(main())

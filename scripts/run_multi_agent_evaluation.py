"""Run the frozen, side-effect-free multi-Agent comparison fixture.

This developer script only reads a synthetic/redacted JSON fixture and writes
the resulting report to stdout.  It never starts a provider, lifecycle,
planner, scheduler, Approval Gate, Gateway, runner, or project store.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_REPOSITORY_ROOT = Path(__file__).resolve().parents[1]

_DEFAULT_MANIFEST = (
    Path(__file__).resolve().parents[1]
    / "tests"
    / "fixtures"
    / "agent_eval"
    / "multi_agent"
    / "manifest.json"
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the offline multi-Agent evaluation fixture.")
    parser.add_argument(
        "--manifest",
        type=Path,
        default=_DEFAULT_MANIFEST,
        help="Synthetic/redacted fixture manifest to read (default: repository multi-Agent manifest).",
    )
    parser.add_argument(
        "--summary",
        action="store_true",
        help="Print comparison metrics and Gate verdict without per-case detail.",
    )
    parser.add_argument(
        "--require-gate",
        action="store_true",
        help="Return a non-zero status when the evidence Gate does not pass.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if str(_REPOSITORY_ROOT) not in sys.path:
        sys.path.insert(0, str(_REPOSITORY_ROOT))
    sys.stdout.reconfigure(encoding="utf-8")
    from src.backend.app.schemas.agent_eval import MultiAgentEvalManifest
    from src.backend.app.services.multi_agent_evaluation_service import (
        MultiAgentEvaluationService,
    )

    manifest = MultiAgentEvalManifest.model_validate_json(args.manifest.read_text(encoding="utf-8"))
    report = MultiAgentEvaluationService().evaluate(manifest)
    payload = (
        {
            "suite_version": report.suite_version,
            "manifest_hash": report.manifest_hash,
            "case_count": report.case_count,
            "baseline_metrics": report.baseline_metrics,
            "candidate_metrics": report.candidate_metrics,
            "gate_passed": report.gate_passed,
            "gate_failures": report.gate_failures,
            "conclusion": report.conclusion,
        }
        if args.summary
        else report.model_dump(mode="json")
    )
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    return 1 if args.require_gate and not report.gate_passed else 0


if __name__ == "__main__":
    raise SystemExit(main())

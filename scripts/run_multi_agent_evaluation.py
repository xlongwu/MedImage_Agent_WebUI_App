"""Validate a frozen G0 run bundle without treating a manifest as observations."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def main() -> int:
    parser = argparse.ArgumentParser(description="Evaluate an append-only real-redacted G0 run bundle.")
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument("--expected-manifest-hash", required=True)
    parser.add_argument("--allow-network", action="store_true")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--summary", action="store_true")
    parser.add_argument("--require-gate", action="store_true")
    args = parser.parse_args()
    try:
        from src.backend.app.schemas.agent_eval import MultiAgentEvalManifest, MultiAgentGateRunBundle
        from src.backend.app.services.multi_agent_evaluation_service import MultiAgentEvaluationService

        if not args.allow_network:
            raise ValueError("MULTI_AGENT_GATE_NETWORK_NOT_ALLOWED")
        manifest = MultiAgentEvalManifest.model_validate_json(args.manifest.read_text(encoding="utf-8"))
        service = MultiAgentEvaluationService()
        actual_hash = service.manifest_hash(manifest)
        if actual_hash != args.expected_manifest_hash:
            raise ValueError("MULTI_AGENT_GATE_MANIFEST_HASH_MISMATCH")
        bundle = MultiAgentGateRunBundle.model_validate_json(args.bundle.read_text(encoding="utf-8").splitlines()[0])
        report = service.evaluate(manifest=manifest, bundle=bundle)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        if args.output.exists():
            raise ValueError("MULTI_AGENT_GATE_REPORT_ALREADY_EXISTS")
        args.output.write_text(json.dumps(report.model_dump(mode="json"), ensure_ascii=False, sort_keys=True, indent=2), encoding="utf-8")
    except (OSError, ValueError) as exc:
        print(json.dumps({"error_code": str(exc) or type(exc).__name__}, ensure_ascii=False))
        return 2
    payload = {"manifest_hash": report.manifest_hash, "case_count": report.case_count, "gate_passed": report.gate_passed, "gate_failures": list(report.gate_failures), "report": str(args.output)}
    print(json.dumps(payload if args.summary else report.model_dump(mode="json"), ensure_ascii=False, sort_keys=True))
    return 1 if args.require_gate and not report.gate_passed else 0


if __name__ == "__main__":
    raise SystemExit(main())

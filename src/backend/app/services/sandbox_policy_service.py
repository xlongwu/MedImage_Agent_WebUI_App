"""Pure construction and verification of reviewed sandbox policy sets."""

from __future__ import annotations

from pathlib import Path

from src.backend.app.core.exceptions import SafetyError
from src.backend.app.planner.audit_record import stable_hash
from src.backend.app.runtime.node_contract_registry import get_node_contract
from src.backend.app.schemas.sandbox import SandboxLimits, SandboxPolicy, SandboxPolicySet


_POLICY_VERSION = "windows-sandbox-v1"
_EMPTY_POLICY_HASH = stable_hash({"schema_version": 1, "policies": []})
_ENVIRONMENT_KEYS = ("SystemRoot", "ComSpec", "PATH")


def empty_policy_set() -> SandboxPolicySet:
    return SandboxPolicySet(policies=(), policies_hash=_EMPTY_POLICY_HASH)


class SandboxPolicyService:
    """Build policy from persisted plan/environment facts without side effects."""

    def build_for_plan(
        self,
        *,
        reviewed_plan,
        environment,
        write_roots: tuple[str, ...],
        readonly_roots: tuple[str, ...],
    ) -> SandboxPolicySet:
        payload = reviewed_plan.payload if hasattr(reviewed_plan, "payload") else {}
        plan = payload.get("plan", {}) if isinstance(payload, dict) else {}
        nodes = plan.get("nodes", []) if isinstance(plan, dict) else []
        policies: list[SandboxPolicy] = []
        backend_paths = {
            capability.backend_id: capability.executable_path_hash
            for capability in environment.backend_capabilities
            if capability.executable_path_hash
        }
        for node in nodes:
            if not isinstance(node, dict):
                continue
            node_id = str(node.get("id") or "")
            if not node_id:
                raise SafetyError("SANDBOX_POLICY_MISSING", code="SANDBOX_POLICY_MISSING")
            contract = get_node_contract(node_id)
            if contract.resources.process_mode != "sandbox_process":
                continue
            executable_hash = backend_paths.get(contract.backend)
            # A disabled contract deliberately receives no process authority.
            # This keeps plan/review usable while external backends remain
            # unavailable and prevents a policy from becoming an enablement
            # mechanism by itself.
            if not contract.executable:
                continue
            if not executable_hash:
                raise SafetyError("SANDBOX_POLICY_MISSING", code="SANDBOX_POLICY_MISSING")
            limits = SandboxLimits(timeout_seconds=600, memory_limit_bytes=2 * 1024**3, max_processes=8)
            identity = {
                "schema_version": 1,
                "policy_version": _POLICY_VERSION,
                "node_id": node_id,
                "backend_id": contract.backend,
                "provider": "windows_restricted_process",
                "executable_id": contract.backend,
                "executable_path_hash": executable_hash,
                "readonly_root_hashes": tuple(sorted(stable_hash(str(Path(root))) for root in readonly_roots)),
                "output_root_hashes": tuple(sorted(stable_hash(str(Path(root))) for root in write_roots)),
                "allowed_environment_keys": _ENVIRONMENT_KEYS,
                "network_isolation": "not_enforced",
                "limits": limits.model_dump(mode="json"),
            }
            policies.append(SandboxPolicy(policy_hash=stable_hash(identity), **identity))
        policies.sort(key=lambda item: item.node_id)
        if not policies:
            return empty_policy_set()
        identity = [item.model_dump(mode="json", exclude={"policy_hash"}) for item in policies]
        return SandboxPolicySet(policies=tuple(policies), policies_hash=stable_hash({"schema_version": 1, "policies": identity}))

    @staticmethod
    def verify(policy_set: SandboxPolicySet) -> None:
        if not policy_set.policies:
            if policy_set.policies_hash != _EMPTY_POLICY_HASH:
                raise SafetyError("EXECUTION_SANDBOX_POLICY_CHANGED", code="EXECUTION_SANDBOX_POLICY_CHANGED")
            return
        expected = stable_hash({
            "schema_version": 1,
            "policies": [item.model_dump(mode="json", exclude={"policy_hash"}) for item in policy_set.policies],
        })
        if expected != policy_set.policies_hash:
            raise SafetyError("EXECUTION_SANDBOX_POLICY_CHANGED", code="EXECUTION_SANDBOX_POLICY_CHANGED")

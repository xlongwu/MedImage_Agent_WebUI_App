"""OpenAI-compatible LLM Provider Adapter.

Translates a natural-language goal + Tool Catalog into an LLM prompt,
sends it to an OpenAI-compatible API, parses the JSON response, and
returns a structured result.

Security:
  - Never reads real API keys unless MEDIMAGE_LLM_API_KEY is set.
  - Never prints or logs API keys.
  - http_post is injectable for testing (no real network in CI).
"""

from __future__ import annotations

import json
import os
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from src.backend.app.schemas.planner_plan import canonical_plan_payload

# ── Config / Result dataclasses ──────────────────────────────────────────────

@dataclass(frozen=True)
class LLMProviderConfig:
    provider: str
    base_url: str
    model: str
    api_key_set: bool


@dataclass(frozen=True)
class LLMProviderResult:
    ok: bool
    content: str
    raw: dict[str, Any] | None = None
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


# ── Helpers ──────────────────────────────────────────────────────────────────

def _get_config() -> LLMProviderConfig:
    return LLMProviderConfig(
        provider="openai_compatible",
        base_url=os.environ.get("MEDIMAGE_LLM_BASE_URL", "https://api.openai.com/v1"),
        model=os.environ.get("MEDIMAGE_LLM_MODEL", "gpt-4.1-mini"),
        api_key_set=bool(os.environ.get("MEDIMAGE_LLM_API_KEY")),
    )


def _tool_catalog_summary() -> list[dict[str, Any]]:
    """Return a compact summary of the Tool Catalog for prompt injection."""
    from src.backend.app.runtime.node_contract_registry import NODE_CONTRACTS  # noqa: E402
    from src.backend.app.runtime.tool_catalog import build_tool_catalog  # noqa: E402

    items = [
        item for item in build_tool_catalog()
        if NODE_CONTRACTS.get(item.id) is not None
        and NODE_CONTRACTS[item.id].executable
    ]
    return [
        {
            "id": item.id,
            "name": item.name,
            "backend": item.backend,
            "requires_approval": item.requires_approval,
            "risk_level": item.risk_level,
            "tags": item.tags,
        }
        for item in items
    ]


# ── Prompt builder ───────────────────────────────────────────────────────────

def build_planner_prompt(
    goal: str,
    tool_catalog: list[dict[str, Any]] | None = None,
    constraints: dict[str, Any] | None = None,
) -> str:
    """Build a strict system prompt for the LLM Planner."""
    if tool_catalog is None:
        tool_catalog = _tool_catalog_summary()

    catalog_json = json.dumps(tool_catalog, ensure_ascii=False, indent=2)
    constraints_json = json.dumps(constraints, ensure_ascii=False) if constraints else "{}"

    return f"""You are a medical imaging pipeline planner. Your ONLY task is to output a valid JSON pipeline plan.

RULES (non-negotiable):
1. ONLY use node IDs from the Tool Catalog below. You MUST NOT invent any node ID.
2. Output STRICT JSON with no explanation, no markdown outside the JSON block.
3. The JSON must have: "pipeline_id" (string) and "nodes" (list).
4. Each node must have: "id", "backend", "depends_on" (list), "params" (dict).
5. For nodes that require approval (requires_approval=true), set params.approved=false.
6. Order nodes so that dependencies appear before dependents.
7. Chain dependencies sequentially where processing order matters.
8. Optional top-level fields are "confidence" (0..1), "missing_prerequisites" (list), and "risks" (list).

TOOL CATALOG (ONLY these node IDs are valid):
{catalog_json}

USER GOAL:
{goal}

CONSTRAINTS:
{constraints_json}

OUTPUT (JSON only):
"""


# ── JSON parser ──────────────────────────────────────────────────────────────

_CODE_FENCE_RE = re.compile(r"```(?:json)?\s*\n?(.*?)```", re.DOTALL)


def parse_llm_plan_json(content: str) -> dict[str, Any]:
    """Extract and parse a pipeline plan JSON from LLM response text.

    Handles:
      - Pure JSON string
      - JSON wrapped in ```json ... ``` code fences
    """
    stripped = content.strip()
    if not stripped:
        raise ValueError("LLM_PLAN_JSON_PARSE_ERROR: empty response content")

    # Try code fence first
    match = _CODE_FENCE_RE.search(stripped)
    if match:
        stripped = match.group(1).strip()

    try:
        data = json.loads(stripped)
    except json.JSONDecodeError as exc:
        raise ValueError(
            f"LLM_PLAN_JSON_PARSE_ERROR: {exc}"
        ) from exc

    if not isinstance(data, dict):
        raise ValueError("LLM_PLAN_JSON_PARSE_ERROR: parsed JSON is not a dict")

    try:
        return canonical_plan_payload(data)
    except Exception as exc:
        raise ValueError(f"LLM_PLAN_SCHEMA_ERROR: {exc}") from exc


# ── Provider caller ──────────────────────────────────────────────────────────

def call_openai_compatible_provider(
    goal: str,
    constraints: dict[str, Any] | None = None,
    http_post: Callable[..., Any] | None = None,
) -> LLMProviderResult:
    """Call an OpenAI-compatible chat completions API.

    Args:
        goal: Natural-language pipeline goal.
        constraints: Optional constraints dict.
        http_post: Injectable HTTP POST function for testing.
                   Signature: (url, headers, json_body, timeout) → response-like.
                   Response must have .json() and .raise_for_status() or .status_code.

    Returns:
        LLMProviderResult with ok=True and parsed plan content on success.
    """
    config = _get_config()

    if not config.api_key_set:
        return LLMProviderResult(
            ok=False,
            content="",
            errors=["LLM_API_KEY_MISSING: set MEDIMAGE_LLM_API_KEY environment variable."],
        )

    prompt = build_planner_prompt(goal, constraints=constraints)
    api_key = os.environ["MEDIMAGE_LLM_API_KEY"]

    body = {
        "model": config.model,
        "messages": [
            {"role": "system", "content": "You are a medical imaging pipeline planner. Output only valid JSON."},
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.1,
        "max_tokens": 4096,
    }
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    url = f"{config.base_url.rstrip('/')}/chat/completions"

    last_raw: dict[str, Any] | None = None
    last_error = "PLANNER_OUTPUT_INVALID"
    for attempt in range(2):
        request_body = dict(body)
        request_body["messages"] = list(body["messages"])
        if attempt == 1:
            request_body["messages"].append(
                {
                    "role": "user",
                    "content": (
                        "The prior response was invalid. Using the identical goal, constraints, "
                        "and tool catalog, return only one corrected JSON object matching the schema."
                    ),
                }
            )
        try:
            if http_post is None:
                import httpx  # noqa: E402
                resp = httpx.post(url, headers=headers, json=request_body, timeout=60.0)
                resp.raise_for_status()
                raw = resp.json()
            else:
                resp = http_post(url, headers, request_body, 60.0)
                if hasattr(resp, "raise_for_status"):
                    resp.raise_for_status()
                raw = resp.json() if hasattr(resp, "json") else resp
        except Exception as exc:
            return LLMProviderResult(
                ok=False,
                content="",
                errors=[f"LLM_API_CALL_FAILED: {type(exc).__name__}"],
            )

        last_raw = raw
        choices = raw.get("choices", []) if isinstance(raw, dict) else []
        content = (
            (choices[0].get("message", {}) or {}).get("content", "")
            if choices else ""
        )
        try:
            candidate = parse_llm_plan_json(content)
            from src.backend.app.planner.plan_validator import validate_plan  # noqa: E402

            validation = validate_plan(candidate)
            if not validation.ok:
                last_error = "; ".join(
                    f"{issue.code}: {issue.message}" for issue in validation.errors
                )
                continue
            return LLMProviderResult(
                ok=True,
                content=json.dumps(candidate, ensure_ascii=False),
                raw=raw,
            )
        except ValueError as exc:
            last_error = str(exc)

    return LLMProviderResult(
        ok=False,
        content="",
        raw=last_raw,
        errors=[f"PLANNER_OUTPUT_INVALID: {last_error}"],
    )


def call_openai_compatible_action_provider(
    *, snapshot: dict[str, Any], repair: bool = False, http_post: Callable[..., Any] | None = None
) -> LLMProviderResult:
    """Request one strict Harness ActionEnvelope from the configured provider.

    This intentionally has no Tool Catalog in its prompt. The returned action
    is validated by both Pydantic and the capability catalog before use.
    """
    config = _get_config()
    if not config.api_key_set:
        return LLMProviderResult(ok=False, content="", errors=["AGENT_HARNESS_PROVIDER_UNAVAILABLE"])
    from src.backend.app.planner.agent_model_adapter import build_action_prompt
    from src.backend.app.schemas.agent_harness import ActionEnvelope

    body = {
        "model": config.model,
        "messages": [
            {"role": "system", "content": "Return strictly valid JSON. Never execute or approve anything."},
            {"role": "user", "content": build_action_prompt(snapshot, repair=repair)},
        ],
        "temperature": 0,
        "max_tokens": 1024,
        "response_format": {"type": "json_object"},
    }
    try:
        if http_post is None:
            import httpx  # noqa: E402
            response = httpx.post(
                f"{config.base_url.rstrip('/')}/chat/completions",
                headers={"Authorization": f"Bearer {os.environ['MEDIMAGE_LLM_API_KEY']}", "Content-Type": "application/json"},
                json=body,
                timeout=60.0,
            )
            response.raise_for_status()
            raw = response.json()
        else:
            response = http_post(
                f"{config.base_url.rstrip('/')}/chat/completions", {}, body, 60.0
            )
            if hasattr(response, "raise_for_status"):
                response.raise_for_status()
            raw = response.json() if hasattr(response, "json") else response
        content = ((raw.get("choices") or [{}])[0].get("message") or {}).get("content", "")
        envelope = ActionEnvelope.model_validate_json(content)
        return LLMProviderResult(ok=True, content=envelope.model_dump_json(), raw=raw)
    except Exception as exc:
        return LLMProviderResult(ok=False, content="", errors=[f"AGENT_HARNESS_MODEL_OUTPUT_INVALID: {exc}"])

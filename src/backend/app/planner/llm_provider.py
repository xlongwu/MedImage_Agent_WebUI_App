"""OpenAI-compatible LLM Provider Adapter.

Translates a natural-language goal + Tool Catalog into an LLM prompt,
sends it to an OpenAI-compatible API, parses the JSON response, and
returns a structured result.

Security:
  - Receives the sole validated Agent model configuration explicitly.
  - Never prints or logs API keys.
  - http_post is injectable for testing (no real network in CI).
"""

from __future__ import annotations

import json
import re
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from src.backend.app.core.config_schema import AgentModelRuntimeConfig
from src.backend.app.schemas.planner_plan import canonical_plan_payload

# ── Config / Result dataclasses ──────────────────────────────────────────────

@dataclass(frozen=True)
class LLMProviderConfig:
    provider: str
    base_url: str
    model: str
    api_key_set: bool
    timeout_seconds: int
    max_output_tokens: int


@dataclass(frozen=True)
class LLMProviderResult:
    ok: bool
    content: str
    raw: dict[str, Any] | None = None
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    model: str | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    cached_input_tokens: int | None = None
    latency_ms: int | None = None
    provider_request_id: str | None = None
    endpoint_class: str = "chat_completions"
    network_called: bool = False


_SAFE_PROVIDER_TEXT = re.compile(r"[^A-Za-z0-9._:/-]+")
_SAFE_ERROR_CODE = re.compile(r"[A-Z][A-Z0-9_]{2,127}")


def _safe_provider_text(value: object, *, limit: int = 128) -> str | None:
    if not isinstance(value, str):
        return None
    cleaned = _SAFE_PROVIDER_TEXT.sub("", value.strip())[:limit]
    return cleaned or None


def _safe_error_code(value: object, *, default: str = "LLM_PROVIDER_ERROR") -> str:
    match = _SAFE_ERROR_CODE.search(str(value or ""))
    return match.group(0) if match else default


def _usage_value(usage: object, *names: str) -> int | None:
    if not isinstance(usage, dict):
        return None
    for name in names:
        value = usage.get(name)
        if isinstance(value, int) and value >= 0:
            return value
    return None


def _provider_request_id(response: object, raw: object) -> str | None:
    headers = getattr(response, "headers", None)
    if headers is not None:
        for name in ("x-request-id", "request-id", "openai-request-id"):
            try:
                value = headers.get(name)
            except AttributeError:
                value = None
            safe = _safe_provider_text(value)
            if safe:
                return safe
    return _safe_provider_text(raw.get("id") if isinstance(raw, dict) else None)


def _result_metadata(
    *, raw: object, response: object, started_at: float, network_called: bool
) -> dict[str, object]:
    usage = raw.get("usage") if isinstance(raw, dict) else None
    prompt_details = usage.get("prompt_tokens_details") if isinstance(usage, dict) else None
    cached = _usage_value(prompt_details, "cached_tokens")
    return {
        "model": _safe_provider_text(raw.get("model") if isinstance(raw, dict) else None),
        "input_tokens": _usage_value(usage, "prompt_tokens", "input_tokens"),
        "output_tokens": _usage_value(usage, "completion_tokens", "output_tokens"),
        "cached_input_tokens": cached,
        "latency_ms": max(0, round((time.perf_counter() - started_at) * 1000)),
        "provider_request_id": _provider_request_id(response, raw),
        "endpoint_class": "chat_completions",
        "network_called": network_called,
    }


# ── Helpers ──────────────────────────────────────────────────────────────────

def _get_config(config: AgentModelRuntimeConfig) -> LLMProviderConfig:
    return LLMProviderConfig(
        provider=config.provider,
        base_url=config.base_url or "",
        model=config.model or "",
        api_key_set=config.api_key is not None,
        timeout_seconds=config.timeout_seconds,
        max_output_tokens=config.max_output_tokens,
    )


def _transport_error_code(exc: Exception) -> str:
    if isinstance(exc, TimeoutError):
        return "AGENT_MODEL_PROVIDER_TIMEOUT"
    try:
        import httpx

        if isinstance(exc, httpx.TimeoutException):
            return "AGENT_MODEL_PROVIDER_TIMEOUT"
    except ImportError:
        pass
    return "AGENT_MODEL_PROVIDER_FAILED"


def _result_fields(result: LLMProviderResult) -> dict[str, object]:
    return {
        "model": result.model,
        "input_tokens": result.input_tokens,
        "output_tokens": result.output_tokens,
        "cached_input_tokens": result.cached_input_tokens,
        "latency_ms": result.latency_ms,
        "provider_request_id": result.provider_request_id,
        "endpoint_class": result.endpoint_class,
        "network_called": result.network_called,
    }


def call_openai_compatible_chat(
    *,
    messages: list[dict[str, str]],
    config: AgentModelRuntimeConfig,
    temperature: float,
    max_output_tokens: int | None = None,
    response_format: dict[str, object] | None = None,
    http_post: Callable[..., Any] | None = None,
) -> LLMProviderResult:
    """Use the single OpenAI-compatible transport and stable error contract."""

    provider_config = _get_config(config)
    if provider_config.provider != "openai_compatible" or config.incomplete_reason() is not None:
        return LLMProviderResult(
            ok=False,
            content="",
            errors=["AGENT_MODEL_CONFIG_INCOMPLETE"],
        )

    body: dict[str, object] = {
        "model": provider_config.model,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_output_tokens or provider_config.max_output_tokens,
    }
    if response_format is not None:
        body["response_format"] = response_format
    headers = {
        "Authorization": (
            f"Bearer {config.api_key.get_secret_value() if config.api_key else ''}"
        ),
        "Content-Type": "application/json",
    }
    url = f"{provider_config.base_url.rstrip('/')}/chat/completions"
    timeout = float(provider_config.timeout_seconds)
    started_at = time.perf_counter()
    try:
        if http_post is None:
            import httpx

            response = httpx.post(
                url,
                headers=headers,
                json=body,
                timeout=timeout,
            )
        else:
            response = http_post(url, headers, body, timeout)
        if hasattr(response, "raise_for_status"):
            response.raise_for_status()
        raw = response.json() if hasattr(response, "json") else response
    except Exception as exc:
        return LLMProviderResult(
            ok=False,
            content="",
            errors=[_transport_error_code(exc)],
            latency_ms=max(0, round((time.perf_counter() - started_at) * 1000)),
            endpoint_class="chat_completions",
            network_called=True,
        )

    content = (
        ((raw.get("choices") or [{}])[0].get("message") or {}).get("content", "")
        if isinstance(raw, dict)
        else ""
    )
    if not isinstance(content, str):
        content = ""
    return LLMProviderResult(
        ok=True,
        content=content,
        raw=raw if isinstance(raw, dict) else None,
        **_result_metadata(
            raw=raw,
            response=response,
            started_at=started_at,
            network_called=True,
        ),
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
    *,
    config: AgentModelRuntimeConfig,
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
    provider_config = _get_config(config)

    if provider_config.provider != "openai_compatible" or config.incomplete_reason() is not None:
        return LLMProviderResult(
            ok=False,
            content="",
            errors=["AGENT_MODEL_CONFIG_INCOMPLETE"],
        )

    prompt = build_planner_prompt(goal, constraints=constraints)
    last_raw: dict[str, Any] | None = None
    last_metadata: dict[str, object] = {"endpoint_class": "chat_completions", "network_called": False}
    last_error = "PLANNER_OUTPUT_INVALID"
    for attempt in range(2):
        messages = [
            {"role": "system", "content": "You are a medical imaging pipeline planner. Output only valid JSON."},
            {"role": "user", "content": prompt},
        ]
        if attempt == 1:
            messages.append(
                {
                    "role": "user",
                    "content": (
                        "The prior response was invalid. Using the identical goal, constraints, "
                        "and tool catalog, return only one corrected JSON object matching the schema."
                    ),
                }
            )
        transport = call_openai_compatible_chat(
            messages=messages,
            config=config,
            temperature=0.1,
            max_output_tokens=provider_config.max_output_tokens,
            http_post=http_post,
        )
        if not transport.ok:
            return transport
        last_raw = transport.raw
        last_metadata = _result_fields(transport)
        content = transport.content
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
                raw=last_raw,
                **last_metadata,
            )
        except ValueError as exc:
            last_error = str(exc)

    return LLMProviderResult(
        ok=False,
        content="",
        raw=last_raw,
        errors=[_safe_error_code(last_error, default="PLANNER_OUTPUT_INVALID")],
        **last_metadata,
    )


def call_openai_compatible_action_provider(
    *, request, config: AgentModelRuntimeConfig, http_post: Callable[..., Any] | None = None
) -> LLMProviderResult:
    """Request one strict Harness ActionEnvelope from the configured provider.

    This intentionally has no Tool Catalog in its prompt. The returned action
    is validated by both Pydantic and the capability catalog before use.
    """
    provider_config = _get_config(config)
    if provider_config.provider != "openai_compatible" or config.incomplete_reason() is not None:
        return LLMProviderResult(ok=False, content="", errors=["AGENT_MODEL_CONFIG_INCOMPLETE"])
    from src.backend.app.planner.agent_model_adapter import build_action_prompt
    from src.backend.app.schemas.agent_harness import parse_action_envelope_json

    transport = call_openai_compatible_chat(
        messages=[
            {"role": "system", "content": request.system_prompt},
            {"role": "user", "content": build_action_prompt(request)},
        ],
        config=config,
        temperature=float(request.model_parameters["temperature"]),
        max_output_tokens=int(request.model_parameters["max_output_tokens"]),
        response_format=request.model_parameters["response_format"],
        http_post=http_post,
    )
    if not transport.ok:
        return transport
    try:
        envelope = parse_action_envelope_json(transport.content)
        return LLMProviderResult(
            ok=True,
            content=envelope.model_dump_json(),
            raw=transport.raw,
            **_result_fields(transport),
        )
    except Exception:
        return LLMProviderResult(
            ok=False,
            content="",
            raw=transport.raw,
            errors=["AGENT_HARNESS_MODEL_OUTPUT_INVALID"],
            **_result_fields(transport),
        )

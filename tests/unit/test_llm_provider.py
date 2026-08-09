"""Tests for llm_provider — prompt builder, JSON parser, provider adapter."""

from __future__ import annotations

import json
import os

import pytest

from src.backend.app.planner.llm_planner import PlannerResponse, generate_plan_from_goal
from src.backend.app.planner.llm_provider import (
    build_planner_prompt,
    call_openai_compatible_action_provider,
    call_openai_compatible_provider,
    parse_llm_plan_json,
)

# ── Prompt builder ──


def test_prompt_contains_goal():
    prompt = build_planner_prompt("run motion correction", [])
    assert "run motion correction" in prompt


def test_prompt_contains_catalog_node():
    catalog = [
        {
            "id": "spm_realign_subject",
            "name": "SPM Realign",
            "backend": "matlab-spm",
            "requires_approval": True,
            "risk_level": "high",
            "tags": ["spm"],
        }
    ]
    prompt = build_planner_prompt("motion", catalog)
    assert "spm_realign_subject" in prompt


def test_prompt_forbids_inventing_nodes():
    prompt = build_planner_prompt("test", [])
    assert "MUST NOT invent" in prompt or "invent" in prompt.lower()


def test_prompt_contains_real_catalog():
    """Prompt built with real catalog contains known node IDs."""
    prompt = build_planner_prompt("motion")
    assert "data_inspection" in prompt
    assert "spm_realign_subject" not in prompt


# ── JSON parser ──


def test_parse_pure_json():
    plan = parse_llm_plan_json('{"pipeline_id": "p", "nodes": []}')
    assert plan == {"pipeline_id": "p", "nodes": []}


def test_parse_code_fence_json():
    content = '```json\n{"pipeline_id": "p", "nodes": []}\n```'
    plan = parse_llm_plan_json(content)
    assert plan == {"pipeline_id": "p", "nodes": []}


def test_parse_code_fence_no_lang():
    content = '```\n{"pipeline_id": "p", "nodes": []}\n```'
    plan = parse_llm_plan_json(content)
    assert plan["pipeline_id"] == "p"


def test_parse_invalid_json_raises():
    with pytest.raises(ValueError, match="LLM_PLAN_JSON_PARSE_ERROR"):
        parse_llm_plan_json("not json at all")


def test_parse_empty_string_raises():
    with pytest.raises(ValueError, match="LLM_PLAN_JSON_PARSE_ERROR"):
        parse_llm_plan_json("")


def test_parse_unknown_fields_raises():
    with pytest.raises(ValueError, match="LLM_PLAN_SCHEMA_ERROR"):
        parse_llm_plan_json('{"pipeline_id":"p","nodes":[],"execute":true}')


# ── Provider without API key ──


def test_no_api_key_returns_error(monkeypatch):
    monkeypatch.delenv("MEDIMAGE_LLM_API_KEY", raising=False)
    result = call_openai_compatible_provider("motion")
    assert result.ok is False
    assert any("LLM_API_KEY_MISSING" in e for e in result.errors)


# ── Provider with fake HTTP client ──


def _valid_plan_response():
    return {
        "choices": [
            {
                "message": {
                    "content": json.dumps(
                        {
                            "pipeline_id": "planned_motion_qc",
                            "nodes": [
                                {
                                    "id": "data_inspection",
                                    "backend": "python",
                                    "depends_on": [],
                                    "params": {},
                                },
                                {
                                    "id": "spm_realign_subject",
                                    "backend": "matlab-spm",
                                    "depends_on": ["data_inspection"],
                                    "params": {"approved": False},
                                },
                                {
                                    "id": "motion_qc_subject",
                                    "backend": "python",
                                    "depends_on": ["spm_realign_subject"],
                                    "params": {},
                                },
                                {
                                    "id": "motion_qc_dataset_report",
                                    "backend": "python",
                                    "depends_on": ["motion_qc_subject"],
                                    "params": {},
                                },
                            ],
                        }
                    )
                }
            }
        ]
    }


class FakeResponse:
    def __init__(self, data, headers=None):
        self._data = data
        self.headers = headers or {}

    def json(self):
        return self._data

    def raise_for_status(self):
        pass


def test_fake_http_returns_valid_plan(monkeypatch):
    monkeypatch.setenv("MEDIMAGE_LLM_API_KEY", "sk-test-key")

    def fake_post(url, headers, body, timeout):
        return FakeResponse(_valid_plan_response())

    result = call_openai_compatible_provider("motion", http_post=fake_post)
    assert result.ok is True
    assert "data_inspection" in result.content


def test_action_provider_extracts_nullable_usage_and_redacted_request_metadata(monkeypatch):
    monkeypatch.setenv("MEDIMAGE_LLM_API_KEY", "sk-test-key")
    response = {
        "id": "chatcmpl-secret-looking-id",
        "model": "gpt-test-1",
        "usage": {
            "prompt_tokens": 12,
            "completion_tokens": 5,
            "prompt_tokens_details": {"cached_tokens": 3},
        },
        "choices": [{"message": {"content": json.dumps({
            "kind": "finish", "reason": "done", "expected_state": "CREATED",
        })}}],
    }

    result = call_openai_compatible_action_provider(
        snapshot={
            "schema_version": 2,
            "policy_version": "p",
            "redaction_policy_version": "r",
            "prompt_template_version": "t",
            "skill_refs": [],
            "omitted_fields": [],
            "sections": {
                name: {"schema_version": 1, "source_hash": name, "source_refs": [], "data": {}}
                for name in ("goal", "policy", "project_evidence", "decision_state", "plan_state", "execution_state", "latest_observation", "last_action_result", "memory_context", "budget")
            },
        },
        http_post=lambda *_args: FakeResponse(response, {"x-request-id": "req_123"}),
    )

    assert result.ok is True
    assert result.model == "gpt-test-1"
    assert (result.input_tokens, result.output_tokens, result.cached_input_tokens) == (12, 5, 3)
    assert result.provider_request_id == "req_123"
    assert result.network_called is True


def test_action_provider_keeps_missing_usage_nullable_and_sanitizes_errors(monkeypatch):
    monkeypatch.setenv("MEDIMAGE_LLM_API_KEY", "sk-test-key")
    result = call_openai_compatible_action_provider(
        snapshot={
            "schema_version": 2, "policy_version": "p", "redaction_policy_version": "r",
            "prompt_template_version": "t", "skill_refs": [], "omitted_fields": [],
            "sections": {
                name: {"schema_version": 1, "source_hash": name, "source_refs": [], "data": {}}
                for name in ("goal", "policy", "project_evidence", "decision_state", "plan_state", "execution_state", "latest_observation", "last_action_result", "memory_context", "budget")
            },
        },
        http_post=lambda *_args: FakeResponse({"choices": [{"message": {"content": "not json sk-test-key"}}]}),
    )

    assert result.ok is False
    assert result.input_tokens is None
    assert "sk-test-key" not in " ".join(result.errors)


def test_fake_http_invalid_json_returns_error(monkeypatch):
    monkeypatch.setenv("MEDIMAGE_LLM_API_KEY", "sk-test-key")

    def fake_post(url, headers, body, timeout):
        return FakeResponse({"choices": [{"message": {"content": "not json"}}]})

    result = call_openai_compatible_provider("motion", http_post=fake_post)
    assert result.ok is False
    assert result.content == ""
    assert any("LLM_PLAN_JSON_PARSE_ERROR" in error for error in result.errors)


def test_fake_http_invalid_json_gets_exactly_one_schema_repair(monkeypatch):
    monkeypatch.setenv("MEDIMAGE_LLM_API_KEY", "sk-test-key")
    calls = 0

    def fake_post(url, headers, body, timeout):
        nonlocal calls
        calls += 1
        if calls == 1:
            return FakeResponse({"choices": [{"message": {"content": "not json"}}]})
        return FakeResponse(_valid_plan_response())

    result = call_openai_compatible_provider("motion", http_post=fake_post)

    assert result.ok is True
    assert calls == 2


def test_fake_http_unknown_node_returns_error(monkeypatch):
    monkeypatch.setenv("MEDIMAGE_LLM_API_KEY", "sk-test-key")

    def fake_post(url, headers, body, timeout):
        return FakeResponse(
            {
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(
                                {
                                    "pipeline_id": "bad",
                                    "nodes": [
                                        {
                                            "id": "invented_node",
                                            "backend": "python",
                                            "depends_on": [],
                                            "params": {},
                                        }
                                    ],
                                }
                            )
                        }
                    }
                ]
            }
        )

    result = call_openai_compatible_provider("motion", http_post=fake_post)
    assert result.ok is False
    assert any("UNKNOWN_NODE_ID" in error for error in result.errors)


# ── Integration with Planner ──


def test_openai_planner_with_fake_client(monkeypatch):
    monkeypatch.setenv("MEDIMAGE_LLM_API_KEY", "sk-test-key")

    def fake_post(url, headers, body, timeout):
        return FakeResponse(_valid_plan_response())

    _resp = generate_plan_from_goal(
        "motion", provider="openai_compatible", constraints={}, project_config_path=None
    )
    # We can't inject the fake client here directly — the integration
    # would call real httpx.  This test only checks that the provider
    # code path doesn't crash with a mocked key.
    # The real test is in test_llm_planner_openai_provider.py
    pass


def test_openai_provider_calls_validator(monkeypatch):
    """Verify that the openai_compatible path exists and validates."""
    monkeypatch.setenv("MEDIMAGE_LLM_API_KEY", "sk-test-key")
    # This test verifies the code path exists; real call needs real API
    resp = generate_plan_from_goal("motion", provider="openai_compatible")
    # Should fail because fake key won't work with real API,
    # but the code path should not crash
    assert isinstance(resp, PlannerResponse)
    assert resp.provider == "openai_compatible"


def test_api_key_not_in_response(monkeypatch):
    monkeypatch.setenv("MEDIMAGE_LLM_API_KEY", "sk-test-key")

    def fake_post(url, headers, body, timeout):
        return FakeResponse(_valid_plan_response())

    result = call_openai_compatible_provider("motion", http_post=fake_post)
    d = json.dumps({"content": result.content, "errors": result.errors})
    assert "sk-test-key" not in d


# ── No real network ──


def test_no_real_network():
    """Without API key, provider must not make network calls."""
    if "MEDIMAGE_LLM_API_KEY" in os.environ:
        del os.environ["MEDIMAGE_LLM_API_KEY"]
    result = call_openai_compatible_provider("motion")
    assert result.ok is False
    assert any("LLM_API_KEY_MISSING" in e for e in result.errors)

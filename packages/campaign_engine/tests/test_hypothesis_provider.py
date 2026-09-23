import json
from types import SimpleNamespace
from unittest.mock import Mock

import httpx
import pytest
from campaign_engine.contracts import HypothesisBatch
from campaign_engine.openai_gateway import (
    PlanningUnavailable,
    make_replay,
    propose_hypotheses,
    replay_hypotheses,
    summary_fingerprint,
)
from openai import (
    APIConnectionError,
    APIResponseValidationError,
    APIStatusError,
    APITimeoutError,
    AuthenticationError,
    PermissionDeniedError,
    RateLimitError,
)


@pytest.fixture
def hypothesis_batch():
    return HypothesisBatch.model_validate({"hypotheses": [{
        "campaign": {
            "campaign_name": "Проверка тарифа",
            "target_tariff": "tariff_2",
            "channel": "sms",
            "filter_current_tariff": "tariff_1",
        },
        "rationale": "Проверить исторически перспективный переход пилотом.",
    }]})


@pytest.fixture
def provider_client(hypothesis_batch):
    client = Mock()
    client.responses.parse.return_value = SimpleNamespace(output_parsed=hypothesis_batch)
    return client


@pytest.mark.parametrize("timeout", ["bad", "", "nan", "inf", "-inf", "0", "-1"])
def test_invalid_timeout_configuration_falls_back_without_a_request(
    monkeypatch, provider_client, timeout,
):
    monkeypatch.setenv("OPENAI_TIMEOUT_SECONDS", timeout)
    with pytest.raises(PlanningUnavailable, match="timeout configuration") as error:
        propose_hypotheses({}, client=provider_client)
    assert error.value.code == "config"
    assert error.value.http_status is None
    provider_client.responses.parse.assert_not_called()


@pytest.mark.parametrize("timeout", [0, -0.1, float("nan"), float("inf"), True])
def test_invalid_caller_time_budget_does_not_start_request(provider_client, timeout):
    with pytest.raises(PlanningUnavailable, match="timeout configuration"):
        propose_hypotheses({}, client=provider_client, timeout_seconds=timeout)
    provider_client.responses.parse.assert_not_called()


@pytest.mark.parametrize("budget,expected", [(0.125, 0.125), (12, 12.0), (1000, 30.0)])
def test_request_timeout_respects_caller_budget_and_hard_cap(provider_client, budget, expected):
    propose_hypotheses({}, client=provider_client, timeout_seconds=budget)
    assert provider_client.responses.parse.call_args.kwargs["timeout"] == expected


def test_owned_client_disables_retries_and_bounds_timeout(monkeypatch, provider_client):
    monkeypatch.setenv("OPENAI_API_KEY", "test-key-not-used")
    monkeypatch.setenv("OPENAI_TIMEOUT_SECONDS", "600")
    monkeypatch.delenv("OPENAI_MODEL", raising=False)
    constructor = Mock(return_value=provider_client)
    monkeypatch.setattr("campaign_engine.openai_gateway.OpenAI", constructor)
    propose_hypotheses({"tariffs": ["tariff_2"]})
    constructor.assert_called_once_with(timeout=30.0, max_retries=0)
    options = provider_client.responses.parse.call_args.kwargs
    assert options["model"] == "gpt-6-sol"
    assert options["store"] is False
    assert options["max_output_tokens"] == 4000


def test_client_initialization_failure_is_safe_fallback(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-key-not-used")
    constructor = Mock(side_effect=RuntimeError("secret-provider-details"))
    monkeypatch.setattr("campaign_engine.openai_gateway.OpenAI", constructor)
    with pytest.raises(PlanningUnavailable, match="OpenAI request failed") as error:
        propose_hypotheses({})
    assert "secret-provider-details" not in str(error.value)
    assert error.value.__suppress_context__
    assert error.value.code == "api_error"
    assert error.value.http_status is None


def test_unexpected_provider_response_is_safe_fallback(provider_client):
    provider_client.responses.parse.return_value = object()
    with pytest.raises(PlanningUnavailable, match="invalid output") as error:
        propose_hypotheses({}, client=provider_client)
    assert error.value.code == "invalid_response"


def test_empty_model_falls_back_before_request(monkeypatch, provider_client):
    monkeypatch.setenv("OPENAI_MODEL", "   ")
    with pytest.raises(PlanningUnavailable, match="model is not configured"):
        propose_hypotheses({}, client=provider_client)
    provider_client.responses.parse.assert_not_called()


def test_summary_hash_is_canonical_and_sensitive_to_input():
    summary = {"tariffs": ["tariff_1", "tariff_2"], "counts": {"MID": 15, "LOW": 3}}
    reordered = {"counts": {"LOW": 3, "MID": 15}, "tariffs": ["tariff_1", "tariff_2"]}
    fingerprint = summary_fingerprint(summary)
    assert len(fingerprint) == 64
    assert fingerprint == summary_fingerprint(reordered)
    assert fingerprint != summary_fingerprint({**summary, "counts": {"MID": 16, "LOW": 3}})
    assert fingerprint != summary_fingerprint({**summary, "tariffs": ["tariff_2", "tariff_1"]})


@pytest.mark.parametrize("summary", [
    {"effect": float("nan")}, {"effect": float("inf")}, {"bad": {1: "ambiguous"}},
    {"bad": {"set"}}, {"bad": (1, 2)}, {"bad": "\ud800"}, ["not an object"],
])
def test_non_json_summary_falls_back_before_request(provider_client, summary):
    with pytest.raises(PlanningUnavailable, match="JSON-compatible"):
        summary_fingerprint(summary)
    with pytest.raises(PlanningUnavailable, match="JSON-compatible"):
        propose_hypotheses(summary, client=provider_client)
    provider_client.responses.parse.assert_not_called()


def test_replay_survives_json_roundtrip_without_live_api_configuration(
    monkeypatch, hypothesis_batch,
):
    summary = {"tariffs": ["tariff_1", "tariff_2"], "label": "Агрегаты"}
    monkeypatch.setenv("OPENAI_MODEL", "gpt-6-sol")
    replay = json.loads(json.dumps(make_replay(summary, hypothesis_batch), ensure_ascii=False))
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setenv("OPENAI_MODEL", "a-different-live-model")
    constructor = Mock(side_effect=AssertionError("Replay must not use the network"))
    monkeypatch.setattr("campaign_engine.openai_gateway.OpenAI", constructor)
    assert replay_hypotheses(summary, replay) == hypothesis_batch
    assert replay["model"] == "gpt-6-sol"
    assert "summary" not in replay
    constructor.assert_not_called()


@pytest.mark.parametrize("change,message", [
    ({"version": 2}, "version is unsupported"),
    ({"version": True}, "version is unsupported"),
    ({"prompt_version": "unknown"}, "prompt version is unsupported"),
    ({"model": ""}, "model is invalid"),
    ({"model": 123}, "model is invalid"),
    ({"summary_sha256": "0" * 64}, "does not match"),
    ({"hypotheses": []}, "invalid proposals"),
    ({"hypotheses": [{"observed_lift_ratio": 0.5}]}, "invalid proposals"),
    ({"pilot_history": []}, "invalid format"),
])
def test_replay_rejects_incompatible_or_malformed_artifacts(hypothesis_batch, change, message):
    summary = {"n": 10}
    replay = {**make_replay(summary, hypothesis_batch), **change}
    with pytest.raises(PlanningUnavailable, match=message):
        replay_hypotheses(summary, replay)


def test_replay_from_a_different_audience_is_rejected(hypothesis_batch):
    replay = make_replay({"n": 10}, hypothesis_batch)
    with pytest.raises(PlanningUnavailable, match="does not match"):
        replay_hypotheses({"n": 11}, replay)


def test_replay_revalidates_campaign_schema(hypothesis_batch):
    replay = make_replay({}, hypothesis_batch)
    replay["hypotheses"][0]["campaign"]["filter_arpu_segment"] = "UNKNOWN"
    with pytest.raises(PlanningUnavailable, match="invalid proposals"):
        replay_hypotheses({}, replay)


@pytest.mark.parametrize("error_type,status,code", [
    (APIConnectionError, None, "connection"),
    (APITimeoutError, None, "timeout"),
    (AuthenticationError, 401, "authentication"),
    (PermissionDeniedError, 403, "permission"),
    (RateLimitError, 429, "rate_limit"),
    (APIStatusError, 500, "api_error"),
    (APIResponseValidationError, 200, "invalid_response"),
])
def test_provider_errors_expose_only_safe_reason_and_http_status(
    provider_client, error_type, status, code,
):
    request = httpx.Request("POST", "https://api.openai.com/v1/responses")
    private_details = "private-provider-body-must-never-be-exported"
    if error_type is APITimeoutError:
        provider_error = error_type(request=request)
    elif error_type is APIConnectionError:
        provider_error = error_type(message=private_details, request=request)
    else:
        response = httpx.Response(status, request=request)
        if error_type is APIResponseValidationError:
            provider_error = error_type(response=response, body={"private": private_details})
        else:
            provider_error = error_type(
                private_details, response=response, body={"private": private_details},
            )
    provider_client.responses.parse.side_effect = provider_error
    with pytest.raises(PlanningUnavailable, match="OpenAI request failed") as error:
        propose_hypotheses({}, client=provider_client)
    safe = error.value
    assert safe.code == code
    assert safe.http_status == status
    assert safe.__dict__ == {"code": code, "http_status": status}
    assert private_details not in str(safe)
    assert safe.__suppress_context__


def test_missing_api_key_has_distinct_safe_code(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with pytest.raises(PlanningUnavailable, match="not configured") as error:
        propose_hypotheses({})
    assert error.value.code == "missing_key"
    assert error.value.http_status is None


@pytest.mark.parametrize("output", [None, {"hypotheses": []}, {"unrecognized": "output"}])
def test_refusal_and_schema_failures_have_invalid_response_code(provider_client, output):
    provider_client.responses.parse.return_value = SimpleNamespace(output_parsed=output)
    with pytest.raises(PlanningUnavailable) as error:
        propose_hypotheses({}, client=provider_client)
    assert error.value.code == "invalid_response"
    assert error.value.http_status is None

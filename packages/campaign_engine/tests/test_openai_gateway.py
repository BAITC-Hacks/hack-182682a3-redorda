from types import SimpleNamespace
from unittest.mock import Mock

import httpx
import pytest
from campaign_engine.openai_gateway import PlanningUnavailable, propose_hypotheses
from openai import APITimeoutError


def test_missing_key_is_explicit_and_does_not_call_network(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with pytest.raises(PlanningUnavailable, match="not configured"):
        propose_hypotheses({})


def test_structured_output_uses_responses_and_selected_model(monkeypatch):
    monkeypatch.setenv("OPENAI_MODEL", "gpt-6-luna")
    client = Mock()
    client.responses.parse.return_value = SimpleNamespace(output_parsed={"hypotheses": [{
        "campaign": {"campaign_name": "Pilot", "target_tariff": "tariff_2", "channel": "sms"},
        "rationale": "Проверить гипотезу на пилоте.",
    }]})
    result = propose_hypotheses({"tariffs": ["tariff_2"]}, client=client)
    assert result.hypotheses[0].campaign.target_tariff == "tariff_2"
    kwargs = client.responses.parse.call_args.kwargs
    assert kwargs["model"] == "gpt-6-luna"
    assert kwargs["store"] is False
    assert "temperature" not in kwargs


def test_timeout_has_a_controlled_fallback_signal():
    client = Mock()
    client.responses.parse.side_effect = APITimeoutError(
        request=httpx.Request("POST", "https://api.openai.com/v1/responses")
    )
    with pytest.raises(PlanningUnavailable):
        propose_hypotheses({}, client=client)


@pytest.mark.parametrize("output", [None, {"hypotheses": []}, {"unexpected": "value"}])
def test_refusal_or_invalid_output_has_a_controlled_fallback_signal(output):
    client = Mock()
    client.responses.parse.return_value = SimpleNamespace(output_parsed=output)
    with pytest.raises(PlanningUnavailable):
        propose_hypotheses({}, client=client)

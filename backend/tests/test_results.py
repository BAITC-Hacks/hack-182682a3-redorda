from decimal import Decimal
from types import SimpleNamespace
from uuid import uuid4

import pytest
from apps.campaigns.services import results


class Rows(list):
    def all(self):
        return self

    def order_by(self, field):
        return Rows(sorted(self, key=lambda row: getattr(row, field)))


def saved_run(monkeypatch, *, status="completed", metrics=None, summary=None,
              campaign=None, pilot_cost="12.40"):
    campaign = campaign or {"campaign_name": "Тест", "target_tariff": "tariff_2",
                            "channel": "sms", "filter_arpu_segment": "MID"}
    run = SimpleNamespace(id=uuid4(), status=status, budget=Decimal("100000.00"),
                          max_contacts=15000, max_pilots=20)
    run.result = SimpleNamespace(summary=summary if summary is not None else {})
    run.campaign_results = Rows([SimpleNamespace(rank=1, campaign=campaign,
                                                 metrics=metrics if metrics is not None else {},
                                                 explanation="Причина")])
    run.pilots = Rows([SimpleNamespace(sequence=1, cost=Decimal(pilot_cost), n_customers=10)])

    class Model:
        Status = SimpleNamespace(COMPLETED="completed")
        objects = SimpleNamespace(get=lambda **kwargs: run)

    monkeypatch.setattr(results.apps, "get_model", lambda *args: Model)
    return run


def test_completed_run_keeps_unknown_effects_null_and_pilot_cost(monkeypatch):
    run = saved_run(monkeypatch)
    run.campaign_results[0].explanation = ""
    output = results.get_results(run.id)
    assert output["run_id"] == str(run.id)
    assert output["status"] == "completed"
    assert output["campaigns"][0]["metrics"] == {"cost": None, "n_contacts": None}
    assert output["campaigns"][0]["explanation"] is None
    assert output["totals"]["pilot_cost"] == "12.40"
    assert output["totals"]["campaign_cost"] is None
    assert output["totals"]["total_cost"] is None
    assert output["totals"]["predicted_effect"] is None
    assert output["totals"]["simulator_result"] is None
    assert output["warnings"]


@pytest.mark.parametrize("status", ["draft", "queued", "running", "failed", "cancelled"])
def test_unfinished_run_is_not_ready(monkeypatch, status):
    run = saved_run(monkeypatch, status=status)
    with pytest.raises(results.ResultNotReady):
        results.get_results(run.id)


def test_total_spend_includes_pilot_and_campaign_cost(monkeypatch):
    run = saved_run(monkeypatch, metrics={"cost": "42.60", "n_contacts": 25},
                    summary={"predicted_effect": "100.00"})
    totals = results.get_results(run.id)["totals"]
    assert totals["campaign_cost"] == "42.60"
    assert totals["total_cost"] == "55.00"
    assert totals["total_contacts"] == 35
    assert totals["predicted_effect"] == "100.00"


def test_rejects_invalid_saved_limits_and_values(monkeypatch):
    run = saved_run(monkeypatch)
    run.campaign_results[0].campaign["channel"] = "fax"
    with pytest.raises(results.InvalidSavedResult):
        results.get_results(run.id)
    run.campaign_results[0].campaign["channel"] = "sms"
    run.campaign_results[0].metrics = {"cost": "100001.00"}
    with pytest.raises(results.InvalidSavedResult, match="budget"):
        results.get_results(run.id)


def test_does_not_sum_per_campaign_effects_for_overlap(monkeypatch):
    run = saved_run(monkeypatch, metrics={"cost": "4.00", "gross_lift": "100.00"})
    output = results.get_results(run.id)
    assert output["totals"]["predicted_effect"] is None


def test_known_pilot_cost_still_obeys_budget_when_campaign_cost_is_missing(monkeypatch):
    run = saved_run(monkeypatch, pilot_cost="101.00")
    run.budget = Decimal("100.00")
    with pytest.raises(results.InvalidSavedResult, match="budget"):
        results.get_results(run.id)

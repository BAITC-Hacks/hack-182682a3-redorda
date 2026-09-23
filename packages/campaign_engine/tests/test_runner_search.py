"""Final portfolio search must not change the experiment that produced its beliefs."""

from dataclasses import asdict, replace

import pytest
from campaign_engine.contracts import RunConfig
from campaign_engine.engine_types import EngineOptions
from campaign_engine.portfolio import PortfolioBuilder
from campaign_engine.runner import run_campaigns
from campaign_engine.tests.test_runner import ControlledEnvironment


@pytest.mark.parametrize("policy", ["adaptive", "fixed_200", "evolve"])
def test_search_changes_only_final_portfolio_with_identical_pilot_evidence(monkeypatch, policy):
    options = EngineOptions(policy=policy, screening_pilots=2, candidate_limit=4,
                            scenario_count=8, portfolio_search=False)
    original = PortfolioBuilder.build
    improved_calls = []

    def record(self, candidates, beliefs, pilots, **kwargs):
        if kwargs.get("improve"):
            improved_calls.append(len(pilots))
        return original(self, candidates, beliefs, pilots, **kwargs)

    monkeypatch.setattr(PortfolioBuilder, "build", record)
    config = RunConfig(max_pilots=4)
    control_env = ControlledEnvironment(size=800)
    trial_env = ControlledEnvironment(size=800)
    old = run_campaigns(control_env, config, options=options)
    new = run_campaigns(trial_env, config, options=replace(options, portfolio_search=True))
    assert old.status == new.status == "completed"
    assert [asdict(p) for p in old.pilots] == [asdict(p) for p in new.pilots]
    assert control_env.requests == trial_env.requests
    assert control_env.pilot_history == trial_env.pilot_history
    assert improved_calls == [len(new.pilots)]
    assert old.metadata["portfolio_search"] == {}
    assert new.estimates["objective"] >= old.estimates["objective"]
    assert new.metadata["portfolio_search"]["baseline_objective"] == old.estimates["objective"]
    assert new.stop_reason == old.stop_reason
    old_updates = [e for e in old.events if e["type"] == "portfolio_updated"]
    new_updates = [e for e in new.events if e["type"] == "portfolio_updated"]
    assert old_updates == new_updates


def test_cancellation_during_final_search_retains_pilots_without_publishing_campaigns(monkeypatch):
    stop = False
    original = PortfolioBuilder.build

    def cancel_after_search(self, *args, **kwargs):
        nonlocal stop
        result = original(self, *args, **kwargs)
        if kwargs.get("improve"):
            stop = True
        return result

    monkeypatch.setattr(PortfolioBuilder, "build", cancel_after_search)
    env = ControlledEnvironment(size=800)
    result = run_campaigns(env, RunConfig(max_pilots=2), should_cancel=lambda: stop)
    assert result.status == "cancelled"
    assert len(result.pilots) == len(env.requests) == 2
    assert result.resource_usage["pilot_cost"] == 100000 - env.remaining_budget
    assert result.campaigns == []
    assert result.events[-1]["type"] == "run_cancelled"

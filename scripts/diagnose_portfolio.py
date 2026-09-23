"""Freeze public pilot observations and compare portfolio counterfactuals."""

from __future__ import annotations

import argparse
import contextlib
import copy
import hashlib
import math
import sys
from dataclasses import asdict
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "packages")]

from campaign_engine.beliefs import (  # noqa: E402
    belief_scenarios,
    effect_ratio,
    initialize_beliefs,
)
from campaign_engine.candidates import Arm, build_candidates, load_history  # noqa: E402
from campaign_engine.contracts import RunConfig  # noqa: E402
from campaign_engine.engine_types import EngineOptions  # noqa: E402
from campaign_engine.portfolio import (  # noqa: E402
    PortfolioBuilder,
    expected_best_ratio,
    portfolio_objective,
)
from campaign_engine.runner import run_campaigns  # noqa: E402
from campaign_engine.segments import SegmentIndex  # noqa: E402

from scripts.benchmark_agent import (  # noqa: E402
    evaluate_one,
    load_public_module,
    make_manifest,
    save_report,
)


def forecast_plan(index, channels, beliefs, pilots, campaigns, options, seed=42):
    """Value an ordered plan at its executable ID prefixes with frozen beliefs.

    This diagnostic supports the engine's disjoint final base cells. Pilot
    membership remains unknown and is averaged, just as in the engine forecast.
    """
    draws = belief_scenarios(beliefs, options.scenario_count, seed)
    pilot_effects, pilot_probs, bases = {}, {}, {}
    cost = sum(p.observation["cost"] for p in pilots)
    contacts = sum(p.observation["n_customers"] for p in pilots)
    values = np.full(options.scenario_count, -float(cost))
    for pilot in pilots:
        req, obs = pilot.request, pilot.observation
        if req.get("filter_data_segment") or req.get("filter_call_segment"):
            raise ValueError("Diagnostic requires full-cell pilot requests")
        arm = Arm(req["filter_current_tariff"], req["filter_arpu_segment"], req["target_tariff"])
        cell = (arm.current_tariff, arm.arpu_segment)
        pilot_effects.setdefault(cell, []).append(
            effect_ratio(draws[arm], req["channel"], channels))
        pilot_probs.setdefault(cell, []).append(obs["n_customers"] / len(index.cells[cell]))
    for cell, effects in pilot_effects.items():
        bases[cell] = expected_best_ratio(effects, pilot_probs[cell], options.scenario_count)
        values += float(index.arpu[index.cells[cell]].sum()) * bases[cell]
    seen, details = set(), []
    for campaign in campaigns:
        arm = Arm(campaign["filter_current_tariff"], campaign["filter_arpu_segment"],
                  campaign["target_tariff"])
        cell = (arm.current_tariff, arm.arpu_segment)
        if cell in seen:
            raise ValueError("Diagnostic requires disjoint final base cells")
        seen.add(cell)
        positions = index.positions_for(campaign)
        unit = channels[campaign["channel"]]["cost_per_contact"]
        n = min(len(positions), 5000, max(0, 15000 - contacts))
        if unit:
            n = min(n, int(max(0, 100000 - cost) // unit))
        prefix_arpu = float(index.arpu[positions[:n]].sum())
        effects = pilot_effects.get(cell, []) + [
            effect_ratio(draws[arm], campaign["channel"], channels)]
        best = expected_best_ratio(effects, pilot_probs.get(cell, []) + [1.0],
                                   options.scenario_count)
        incremental = best - bases.get(cell, np.zeros(options.scenario_count))
        contribution = prefix_arpu * incremental - n * unit
        values += contribution
        cost, contacts = cost + n * unit, contacts + n
        details.append({"campaign_name": campaign["campaign_name"], "channel": campaign["channel"],
                        "full_audience": len(positions), "n_contacts": n, "cost": n * unit,
                        "full_audience_arpu": float(index.arpu[positions].sum()),
                        "contacted_prefix_arpu": prefix_arpu,
                        "estimated_incremental_net": float(contribution.mean())})
    return {"net_arpu_gain_mean": float(values.mean()),
            "objective": portfolio_objective(values, options.risk_weight),
            "total_cost": cost, "total_contacts": contacts, "campaigns": details}


class SnapshotAgent:
    def __init__(self, history, policy):
        self.history, self.options = history, EngineOptions(policy=policy, portfolio_search=False)
        self.last_result = None

    def act(self, env):
        self.index = SegmentIndex(env.customer_profile)
        self.channels = copy.deepcopy(env.channels)
        self.candidates = build_candidates(self.index, env.tariffs, self.history,
                                           self.options.candidate_limit)
        self.last_result = run_campaigns(env, history=self.history, options=self.options)
        if self.last_result.status != "completed":
            raise ValueError(self.last_result.stop_reason)
        self.beliefs = initialize_beliefs(self.candidates)
        for pilot in self.last_result.pilots:
            req, obs = pilot.request, pilot.observation
            arm = Arm(req["filter_current_tariff"], req["filter_arpu_segment"],
                      req["target_tariff"])
            self.beliefs[arm] = self.beliefs[arm].updated(
                obs["observed_lift_ratio"], obs["n_customers"],
                self.channels[req["channel"]]["conversion_multiplier"])
            if self.beliefs[arm].summary() != pilot.posterior:
                raise ValueError("Reconstructed posterior differs from recorded observation update")
        self.budget, self.contacts = env.remaining_budget, env.remaining_contacts
        return self.last_result.campaigns


class ReplayAgent:
    def __init__(self, pilots, campaigns, public_observations):
        self.pilots, self.campaigns = pilots, campaigns
        self.public_observations = public_observations
        self.matched_observations = 0

    def act(self, env):
        for pilot, expected in zip(self.pilots, self.public_observations, strict=True):
            observed = env.run_pilot(**pilot.request)
            if observed != expected:
                raise ValueError("Pilot replay changed a public observation")
            self.matched_observations += 1
        return copy.deepcopy(self.campaigns)


def check_resources(forecast, record, n_pilots):
    finals = record["campaigns_detail"][n_pilots:]
    if len(finals) != len(forecast["campaigns"]):
        raise ValueError("Scorer returned a different number of final campaigns")
    for expected, actual in zip(forecast["campaigns"], finals, strict=True):
        if (expected["n_contacts"] != actual["n_contacts"]
                or expected["cost"] != actual["cost"]):
            raise ValueError("Forecast and scorer disagree on a campaign's resource use")
    if (forecast["total_cost"] != record["total_cost"]
            or forecast["total_contacts"] != record["total_contacts"]):
        raise ValueError("Forecast and scorer disagree on total resources")


def diagnose(evaluator, history, policy, seed):
    delegate = SnapshotAgent(history, policy)
    original = evaluate_one(evaluator, delegate, policy, seed)
    if not original["valid"]:
        raise ValueError(original["error"])
    pilots, original_plan = delegate.last_result.pilots, original["campaigns"]
    first_cell = (original_plan[0]["filter_current_tariff"],
                  original_plan[0]["filter_arpu_segment"])
    builder = PortfolioBuilder(delegate.index, delegate.channels,
                               delegate.options, RunConfig().seed)
    variants = {"original": original_plan}
    rebuilt = builder.build(delegate.candidates, delegate.beliefs, pilots,
                            budget=delegate.budget, contacts=delegate.contacts,
                            official_budget=delegate.budget, official_contacts=delegate.contacts)
    if rebuilt.campaigns != original_plan:
        raise ValueError("Frozen observations did not reconstruct the original portfolio")
    for channel in ("sms", "push"):
        swapped = copy.deepcopy(original_plan)
        swapped[0]["channel"] = channel
        variants[f"first_{channel}"] = swapped
        variants[f"rebuild_first_cell_{channel}"] = builder.build(
            delegate.candidates, delegate.beliefs, pilots,
            budget=delegate.budget, contacts=delegate.contacts,
            official_budget=delegate.budget, official_contacts=delegate.contacts,
            allowed_channels={first_cell: {channel}}).campaigns
    records = []
    for name, plan in variants.items():
        forecast = forecast_plan(delegate.index, delegate.channels, delegate.beliefs, pilots,
                                 plan, delegate.options)
        dropped = [c["campaign_name"] for c in forecast["campaigns"] if not c["n_contacts"]]
        if dropped:
            plan = [c for c in plan if c["campaign_name"] not in dropped]
            forecast = forecast_plan(delegate.index, delegate.channels, delegate.beliefs, pilots,
                                     plan, delegate.options)
        replay = ReplayAgent(pilots, plan, original["pilot_observations"])
        scored = evaluate_one(evaluator, replay, name, seed)
        if not scored["valid"]:
            raise ValueError(scored["error"])
        check_resources(forecast, scored, len(pilots))
        if name == "original":
            if not math.isclose(forecast["net_arpu_gain_mean"], rebuilt.mean_net, abs_tol=1e-6):
                raise ValueError("Independent forecast differs from builder's valuation")
            if scored["net_arpu_gain"] != original["net_arpu_gain"]:
                raise ValueError("Original replay changed the scored net result")
        records.append({"variant": name, "forecast": forecast, "scored": scored,
                        "matching_pilot_observations": replay.matched_observations,
                        "resource_accounting_matches": True, "dropped_zero_contact_rows": dropped,
                        "actual_net_change": scored["net_arpu_gain"] - original["net_arpu_gain"]})
    return {"seed": seed, "pilot_policy": policy, "first_cell": first_cell,
            "snapshot": {"options": asdict(delegate.options),
                         "candidates": [asdict(c) for c in delegate.candidates],
                         "pilots": [asdict(p) for p in pilots],
                         "public_observations": original["pilot_observations"],
                         "remaining_budget": delegate.budget,
                         "remaining_contacts": delegate.contacts,
                         "original_estimates": delegate.last_result.estimates},
            "variants": records}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--kit", type=Path, default=ROOT / "data/participant-kit")
    parser.add_argument("--seeds", nargs="+", type=int, default=[42])
    parser.add_argument("--policy", choices=["adaptive", "fixed_200"], default="adaptive")
    parser.add_argument("--output", type=Path,
                        default=ROOT / "artifacts/portfolio_diagnostics.json")
    args = parser.parse_args()
    kit, output = args.kit.resolve(), args.output.resolve()
    sys.path.insert(0, str(kit))
    report = {"format_version": 1, "complete": False,
              "scope": "Dev diagnostics with identical public pilot requests and observations",
              "limitations": ["Same historical mock as the original benchmark; no holdout claim.",
                              "The forecast averages pilot membership; net need not match scorer.",
                              "Whole rebuild restricts the first cell's channel, not other cells.",
                              "Diagnostic variants are not selected on scored net."],
              "manifest": make_manifest(ROOT, kit, [args.policy], len(args.seeds)), "runs": []}
    report["manifest"]["environment_seeds"] = args.seeds
    report["manifest"]["diagnostic_source_sha256"] = hashlib.sha256(
        Path(__file__).read_bytes()).hexdigest()
    history = load_history(kit)
    with contextlib.chdir(kit):
        evaluator = load_public_module("redorda_portfolio_public_eval", kit / "local_eval.py")
        for seed in args.seeds:
            run = diagnose(evaluator, history, args.policy, seed)
            report["runs"].append(run)
            save_report(output, report)
            for variant in run["variants"]:
                print(f"seed={seed} {variant['variant']}: "
                      f"forecast={variant['forecast']['net_arpu_gain_mean']:.0f}, "
                      f"net={variant['scored']['net_arpu_gain']:.0f}, "
                      f"delta={variant['actual_net_change']:+.0f}, resources=match", flush=True)
    report["complete"] = True
    save_report(output, report)


if __name__ == "__main__":
    main()

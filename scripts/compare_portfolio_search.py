"""Dev-only portfolio comparison with identical fixed 10 x 200 SMS observations."""

from __future__ import annotations

import argparse
import contextlib
import copy
import hashlib
import json
import math
import sys
import time
from dataclasses import asdict, replace
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "packages")]

from campaign_engine.beliefs import initialize_beliefs  # noqa: E402
from campaign_engine.candidates import Arm, build_candidates, load_history  # noqa: E402
from campaign_engine.contracts import RunConfig  # noqa: E402
from campaign_engine.engine_types import EngineOptions  # noqa: E402
from campaign_engine.portfolio import PortfolioBuilder  # noqa: E402
from campaign_engine.runner import run_campaigns  # noqa: E402
from campaign_engine.segments import SegmentIndex  # noqa: E402

from scripts.benchmark_agent import (  # noqa: E402
    evaluate_one,
    load_public_module,
    make_manifest,
    numeric_summary,
    save_report,
)
from scripts.diagnose_portfolio import (  # noqa: E402
    ReplayAgent,
    SnapshotAgent,
    check_resources,
    forecast_plan,
)


class FrozenSnapshotAgent(SnapshotAgent):
    """Keep search disabled throughout observation collection, including finalization."""

    def __init__(self, history, options=None, engine_seed=42):
        super().__init__(history, "fixed_200")
        self.options = replace(options or EngineOptions(), policy="fixed_200",
                               portfolio_search=False, pilot_contact_cap=2000)
        self.config = RunConfig(seed=engine_seed)

    def act(self, env):
        self.index = SegmentIndex(env.customer_profile)
        self.channels = copy.deepcopy(env.channels)
        self.candidates = build_candidates(self.index, env.tariffs, self.history,
                                           self.options.candidate_limit)
        self.last_result = run_campaigns(env, self.config, history=self.history,
                                         options=self.options)
        if self.last_result.status != "completed":
            raise ValueError(self.last_result.stop_reason)
        self.beliefs = initialize_beliefs(self.candidates)
        for pilot in self.last_result.pilots:
            request, observation = pilot.request, pilot.observation
            arm = Arm(request["filter_current_tariff"], request["filter_arpu_segment"],
                      request["target_tariff"])
            self.beliefs[arm] = self.beliefs[arm].updated(
                observation["observed_lift_ratio"], observation["n_customers"],
                self.channels[request["channel"]]["conversion_multiplier"])
            if self.beliefs[arm].summary() != pilot.posterior:
                raise ValueError("Frozen posterior differs from the recorded pilot update")
        self.budget, self.contacts = env.remaining_budget, env.remaining_contacts
        return self.last_result.campaigns


def validate_fixed_schedule(pilots, public_observations):
    if len(pilots) != 10 or len(public_observations) != 10:
        raise ValueError("Comparison requires exactly 10 successful fixed_200 pilots")
    for number, (pilot, public) in enumerate(zip(pilots, public_observations, strict=True), 1):
        if pilot.request.get("channel") != "sms" or pilot.request.get("n_customers") != 200:
            raise ValueError(f"Pilot {number} must request exactly 200 SMS contacts")
        if pilot.observation.get("n_customers") != 200 or public.get("n_customers") != 200:
            raise ValueError(f"Pilot {number} has a capped audience; 200 actual contacts required")
        if pilot.observation.get("cost") != 800 or public.get("cost") != 800:
            raise ValueError(f"Pilot {number} cost differs from 200 SMS contacts")
        for field in ("n_customers", "cost", "observed_lift_ratio"):
            if pilot.observation.get(field) != public.get(field):
                raise ValueError(f"Pilot {number} differs from its recorded public observation")


def snapshot_payload(delegate, public_observations):
    return {
        "config": delegate.config.model_dump(), "options": asdict(delegate.options),
        "candidates": [asdict(candidate) for candidate in delegate.candidates],
        "beliefs": [{"arm": asdict(arm), "posterior": belief.summary()}
                    for arm, belief in sorted(delegate.beliefs.items())],
        "pilots": [asdict(pilot) for pilot in delegate.last_result.pilots],
        "public_observations": copy.deepcopy(public_observations),
        "channels": copy.deepcopy(delegate.channels),
        "remaining_budget": delegate.budget, "remaining_contacts": delegate.contacts,
    }


def _fingerprint(value):
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(encoded.encode()).hexdigest()


def check_valuation(portfolio, forecast, pilots):
    for actual, expected in ((portfolio.mean_net, forecast["net_arpu_gain_mean"]),
                             (portfolio.objective, forecast["objective"])):
        if not math.isclose(actual, expected, rel_tol=1e-10, abs_tol=1e-6):
            raise ValueError("Independent frozen forecast differs from portfolio valuation")
    cost = sum(pilot.observation["cost"] for pilot in pilots)
    contacts = sum(pilot.observation["n_customers"] for pilot in pilots)
    if (portfolio.cost + cost != forecast["total_cost"]
            or portfolio.contacts + contacts != forecast["total_contacts"]):
        raise ValueError("Independent frozen forecast differs from portfolio resources")
    if len(portfolio.details) != len(forecast["campaigns"]):
        raise ValueError("Portfolio campaign metrics are incomplete")
    for declared, independent in zip(portfolio.details, forecast["campaigns"], strict=True):
        if any(declared[key] != independent[key] for key in ("cost", "n_contacts")):
            raise ValueError("Portfolio campaign metrics differ from executable resource use")
        if not math.isclose(declared["estimated_incremental_net"],
                            independent["estimated_incremental_net"],
                            rel_tol=1e-10, abs_tol=1e-6):
            raise ValueError("Portfolio campaign metrics differ from the independent forecast")


def compare_seed(evaluator, history, seed, *, options=None, engine_seed=42):
    """Retain invalid attempts and partial evidence instead of dropping failed pairs."""
    record = {"environment_seed": seed, "engine_seed": engine_seed, "valid": False,
              "error": None, "failure_stage": None, "variants": {}}
    started = time.monotonic()
    stage = "snapshot"
    try:
        delegate = FrozenSnapshotAgent(history, options, engine_seed)
        original = evaluate_one(evaluator, delegate, "fixed_200_snapshot", seed)
        record["snapshot_score"] = original
        if not original["valid"]:
            raise ValueError(original["error"] or "Snapshot evaluation was invalid")
        pilots, observations = delegate.last_result.pilots, original["pilot_observations"]
        stage = "fixed_schedule"
        validate_fixed_schedule(pilots, observations)
        frozen = snapshot_payload(delegate, observations)
        fingerprint = _fingerprint(frozen)
        record.update(snapshot=frozen, snapshot_sha256=fingerprint)
        portfolios = {}
        for name, improve in (("greedy", False), ("search", True)):
            stage = f"{name}_build"
            builder = PortfolioBuilder(delegate.index, delegate.channels, delegate.options,
                                       engine_seed)
            build_started = time.monotonic()
            portfolio = builder.build(
                delegate.candidates, delegate.beliefs, pilots, budget=delegate.budget,
                contacts=delegate.contacts, official_budget=delegate.budget,
                official_contacts=delegate.contacts, improve=improve)
            duration = time.monotonic() - build_started
            variant = {"valid": False, "campaigns": portfolio.campaigns,
                       "build_seconds": duration, "search_metadata": portfolio.search_metadata,
                       "candidate_evaluations": portfolio.search_metadata.get("evaluations", 0)}
            record["variants"][name] = variant
            portfolios[name] = portfolio
            evaluations = variant["candidate_evaluations"]
            max_evaluations = delegate.options.portfolio_search_max_evaluations
            if improve and (type(evaluations) is not int
                            or not 0 <= evaluations <= max_evaluations):
                raise ValueError("Search reports an invalid candidate evaluation count")
            if _fingerprint(snapshot_payload(delegate, observations)) != fingerprint:
                raise ValueError("Portfolio builder mutated the frozen observations or beliefs")
            if name == "greedy" and portfolio.campaigns != original["campaigns"]:
                raise ValueError("Legacy greedy did not reconstruct the frozen snapshot plan")
            stage = f"{name}_forecast"
            forecast = forecast_plan(delegate.index, delegate.channels, delegate.beliefs, pilots,
                                     portfolio.campaigns, delegate.options, seed=engine_seed)
            variant["forecast"] = forecast
            check_valuation(portfolio, forecast, pilots)
            stage = f"{name}_replay"
            replay = ReplayAgent(pilots, portfolio.campaigns, observations)
            scored = evaluate_one(evaluator, replay, name, seed)
            variant.update(scored=scored, matching_pilot_observations=replay.matched_observations)
            if not scored["valid"]:
                raise ValueError(scored["error"] or "Replay evaluation was invalid")
            if replay.matched_observations != 10 or scored["pilot_observations"] != observations:
                raise ValueError("Replay did not preserve all 10 public pilot observations")
            stage = f"{name}_accounting"
            check_resources(forecast, scored, len(pilots))
            if name == "greedy" and scored["net_arpu_gain"] != original["net_arpu_gain"]:
                raise ValueError("Legacy replay changed the snapshot's scored net result")
            variant.update(valid=True, resource_accounting_matches=True)
        stage = "non_regression"
        if portfolios["search"].objective < portfolios["greedy"].objective - 1e-6:
            raise ValueError("Portfolio search reduced the frozen forecast objective")
        record.update(
            valid=True,
            forecast_objective_change=(portfolios["search"].objective
                                       - portfolios["greedy"].objective),
            actual_net_change=(record["variants"]["search"]["scored"]["net_arpu_gain"]
                               - record["variants"]["greedy"]["scored"]["net_arpu_gain"]),
        )
    except Exception as exc:
        record.update(error=f"{type(exc).__name__}: {exc}", failure_stage=stage)
    record["elapsed_seconds"] = time.monotonic() - started
    return record


def summarize(records):
    valid = [record for record in records if record["valid"]]
    delta = [record["actual_net_change"] for record in valid]
    return {
        "attempted_pairs": len(records), "valid_pairs": len(valid),
        "invalid_pairs": len(records) - len(valid),
        "invalid_seeds": [record["environment_seed"] for record in records if not record["valid"]],
        "actual_net_change": numeric_summary(delta),
        "wins": sum(value > 0 for value in delta), "ties": sum(value == 0 for value in delta),
        "losses": sum(value < 0 for value in delta),
        "forecast_objective_change": numeric_summary(
            [record["forecast_objective_change"] for record in valid]),
        "variants": {name: {
            "actual_net": numeric_summary(
                [record["variants"][name]["scored"]["net_arpu_gain"] for record in valid]),
            "build_seconds": numeric_summary(
                [record["variants"][name]["build_seconds"] for record in valid]),
            "candidate_evaluations": numeric_summary(
                [record["variants"][name]["candidate_evaluations"] for record in valid]),
        } for name in ("greedy", "search")},
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--kit", type=Path, default=ROOT / "data/participant-kit")
    parser.add_argument("--seeds", type=int, nargs="+", default=list(range(10)))
    parser.add_argument("--engine-seed", type=int, default=42)
    parser.add_argument("--search-max-evaluations", type=int, default=256)
    parser.add_argument("--search-seconds", type=float, default=10.0)
    parser.add_argument("--output", type=Path,
                        default=ROOT / "artifacts/portfolio_search_dev.json")
    args = parser.parse_args()
    if len(args.seeds) != len(set(args.seeds)) or any(seed < 0 for seed in args.seeds):
        parser.error("--seeds must be unique nonnegative integers")
    try:
        options = EngineOptions(policy="fixed_200", portfolio_search=False,
                                portfolio_search_max_evaluations=args.search_max_evaluations,
                                portfolio_search_seconds=args.search_seconds)
        RunConfig(seed=args.engine_seed)
    except ValueError as exc:
        parser.error(str(exc))
    kit, output = args.kit.resolve(), args.output.resolve()
    if not (kit / "local_eval.py").is_file():
        parser.error("Install the participant kit before running this comparison")
    manifest = make_manifest(ROOT, kit, ["fixed_200"], len(args.seeds))
    manifest.update(environment_seeds=args.seeds,
                    engine_config=RunConfig(seed=args.engine_seed).model_dump(),
                    engine_options={"fixed_200": asdict(options)},
                    search_invocations={"greedy": {"improve": False}, "search": {"improve": True}},
                    diagnostic_sources_sha256={str(path.relative_to(ROOT)): hashlib.sha256(
                        path.read_bytes()).hexdigest() for path in (
                            Path(__file__), ROOT / "scripts/diagnose_portfolio.py")})
    report = {"format_version": 1, "scope": "dev_only", "holdout": False, "complete": False,
              "all_runs_valid": False, "manifest": manifest, "records": [], "summary": {},
              "limitations": [
                  "Development comparison on the historical local mock; no holdout claim.",
                  "Both plans replay identical 10 x 200 SMS requests and public observations.",
                  "Search selects by the frozen forecast objective, never by the scorer result.",
                  "Forecast improvement need not improve scored net; all negative deltas are kept.",
                  "Timing and evaluation counts are reported; no confidence intervals.",
              ]}
    save_report(output, report)
    sys.path.insert(0, str(kit))
    history = load_history(kit)
    with contextlib.chdir(kit):
        evaluator = load_public_module("redorda_search_public_eval", kit / "local_eval.py")
        for seed in args.seeds:
            record = compare_seed(evaluator, history, seed, options=options,
                                  engine_seed=args.engine_seed)
            report["records"].append(record)
            report["summary"] = summarize(report["records"])
            save_report(output, report)
            if record["valid"]:
                before, after = [record["variants"][name] for name in ("greedy", "search")]
                print(f"seed={seed}: net {before['scored']['net_arpu_gain']:.0f} -> "
                      f"{after['scored']['net_arpu_gain']:.0f}, "
                      f"delta={record['actual_net_change']:+.0f}, "
                      f"evaluations={after['candidate_evaluations']}, "
                      f"build={after['build_seconds']:.2f}s, resources=match", flush=True)
            else:
                print(f"seed={seed}: INVALID at {record['failure_stage']}: "
                      f"{record['error']}", flush=True)
    report.update(complete=True,
                  all_runs_valid=all(record["valid"] for record in report["records"]))
    save_report(output, report)
    raise SystemExit(0 if report["all_runs_valid"] else 1)


if __name__ == "__main__":
    main()

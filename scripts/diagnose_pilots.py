"""Controlled pilot-choice diagnostics on local and synthetic environments."""

from __future__ import annotations

import argparse
import contextlib
import copy
import hashlib
import json
import sys
from dataclasses import asdict, replace
from pathlib import Path

import pandas as pd

SYNTHETIC_FAMILIES = ("history_independent", "ranking_reversed", "sparse_positive")
GENERATOR_VERSION = 1


def policy_options(include_screen1: bool = False) -> dict:
    from campaign_engine.engine_types import EngineOptions

    common = EngineOptions(policy="fixed_200", screening_pilots=8)
    options = {
        "fixed_200": common,
        "adaptive_choice_200": replace(common, policy="adaptive_choice_200"),
    }
    if include_screen1:
        options["adaptive_choice_200_screen1"] = replace(
            common, policy="adaptive_choice_200", screening_pilots=1,
        )
    return options


def _effect_uniforms(family: str, model_seed: int, arm: tuple) -> list[float]:
    identity = json.dumps([GENERATOR_VERSION, family, model_seed, *arm], separators=(",", ":"))
    digest = hashlib.sha256(identity.encode("utf-8")).digest()
    return [int.from_bytes(digest[offset:offset + 8], "big") / 2**64 for offset in (0, 8, 16)]


def synthetic_effects(tariffs: pd.DataFrame, history, family: str, model_seed: int) -> pd.DataFrame:
    """Build fixed per-arm effects for the evaluator, independent of pilot observations."""
    from campaign_engine.candidates import _candidate_history_scores

    if family not in SYNTHETIC_FAMILIES:
        raise ValueError("Unknown synthetic family")
    codes = sorted(set(tariffs["tariff_plan_code"]))
    historical_ranks = {}
    if family == "ranking_reversed":
        scores = _candidate_history_scores(history, set(codes))
        for current in codes:
            for segment in ("LOW", "MID", "HIGH"):
                ordered = sorted(
                    (arm for arm in scores
                     if arm.current_tariff == current and arm.arpu_segment == segment),
                    key=lambda arm: (-scores[arm][0], arm),
                )
                for rank, arm in enumerate(ordered):
                    historical_ranks[current, segment, arm.target_tariff] = (
                        rank / (len(ordered) - 1) if len(ordered) > 1 else 0.0
                    )
    rows = []
    for current in codes:
        for segment in ("LOW", "MID", "HIGH"):
            for target in codes:
                arm = current, segment, target
                u, v, w = _effect_uniforms(family, model_seed, arm)
                change = -0.35 + 1.10 * u
                conversion = 0.20 + 0.65 * v
                if family == "ranking_reversed" and arm in historical_ranks:
                    change = -0.45 + 1.10 * historical_ranks[arm] + 0.10 * (u - 0.5)
                elif family == "sparse_positive":
                    change = 0.35 + 0.90 * v if u < 0.12 else -0.02 - 0.25 * v
                    conversion = 0.20 + 0.65 * w
                if current == target:
                    change = 0.0
                rows.append({
                    "tariff_plan_code_from": current,
                    "arpu_segment": segment,
                    "tariff_plan_code_to": target,
                    "arpu_change_pct": change,
                    "conversion_rate": conversion,
                })
    return pd.DataFrame(rows)


class SyntheticEvaluator:
    """Expose the same evaluation call while keeping synthetic effects outside the agent."""

    def __init__(self, profile, tariffs, effects, environment_module, scoring_module):
        self.profile = profile
        self.tariffs = tariffs
        self.effects = effects
        self.environment = environment_module
        self.scoring = scoring_module

    def evaluate_agent(self, agent, seed=None, verbose=False):
        env, execution = self.environment.make_environment(
            self.profile, self.effects, self.tariffs, copy.deepcopy(self.scoring.CHANNELS),
            self.scoring.TOTAL_BUDGET, self.scoring.MAX_TOTAL_CONTACTS,
            lambda *_: (0.0, 0.0), seed=seed,
        )
        try:
            final = agent.act(env)
        except Exception:
            final = []
        final = self.scoring.sanitize_campaigns(final, env.tariffs)[:self.scoring.MAX_CAMPAIGNS]
        pilots = execution.executed_pilot_campaigns()
        campaigns = pd.DataFrame(pilots + final)
        if campaigns.empty:
            return None
        for column in ("filter_current_tariff", "filter_arpu_segment", "filter_data_segment",
                       "filter_call_segment", "explicit_ids"):
            if column not in campaigns:
                campaigns[column] = None
        result = self.scoring.score_campaigns(
            campaigns, env.customer_profile, self.effects, self.tariffs,
            float(env.customer_profile["predicted_arpu"].sum()),
            lambda *_: (0.0, 0.0), team_id="synthetic_diagnostic",
        )
        result["n_pilots"] = len(pilots)
        if verbose:
            self.scoring.print_result(result)
        return result


def pilot_protocol_flags(record: dict) -> dict:
    requests = record.get("pilot_requests", [])
    observations = record.get("pilot_observations", [])
    requested = [request.get("n_customers") for request in requests]
    actual = [observation.get("n_customers") for observation in observations]
    cost = sum(observation.get("cost", 0) for observation in observations)
    exact = (len(requests) == len(observations) == 10
             and all(n == 200 for n in requested + actual)
             and all(request.get("channel") == "sms" for request in requests)
             and cost == 8000)
    return {
        "exact_ten_sms_pilots_of_200": exact,
        "requested_sizes": requested,
        "actual_sizes": actual,
        "pilot_contacts": sum(actual) if all(isinstance(n, int) for n in actual) else None,
        "pilot_cost": cost,
        "distinct_arms": len({
            (request.get("filter_current_tariff"), request.get("filter_arpu_segment"),
             request.get("target_tariff")) for request in requests
        }),
    }


def family_comparisons(records: list[dict], policies: list[str]) -> list[dict]:
    from scripts.benchmark_agent import numeric_summary

    comparisons = []
    for family in sorted({record["family"] for record in records}):
        by_policy = {
            policy: {(record["model_seed"], record["seed"]): record for record in records
                     if record["family"] == family and record["policy"] == policy}
            for policy in policies
        }
        baseline = by_policy["fixed_200"]
        for policy in policies:
            if policy == "fixed_200":
                continue
            candidate = by_policy[policy]
            keys = sorted(set(baseline) | set(candidate), key=str)
            valid = [key for key in keys if key in baseline and key in candidate
                     and baseline[key]["valid"] and candidate[key]["valid"]]
            prefix_length = 1 if policy.endswith("_screen1") else 8
            prefix_checks = {
                key: {
                    "model_seed": key[0], "noise_seed": key[1], "required_pilots": prefix_length,
                    "requests_match": (
                        len(baseline[key].get("pilot_requests", [])) >= prefix_length
                        and len(candidate[key].get("pilot_requests", [])) >= prefix_length
                        and baseline[key]["pilot_requests"][:prefix_length]
                        == candidate[key]["pilot_requests"][:prefix_length]
                    ),
                    "public_observations_match": (
                        len(baseline[key].get("pilot_observations", [])) >= prefix_length
                        and len(candidate[key].get("pilot_observations", [])) >= prefix_length
                        and baseline[key]["pilot_observations"][:prefix_length]
                        == candidate[key]["pilot_observations"][:prefix_length]
                    ),
                }
                for key in valid
            }
            controlled = [key for key in valid if all(
                runs[key]["pilot_protocol"]["exact_ten_sms_pilots_of_200"]
                for runs in (baseline, candidate)
            ) and prefix_checks[key]["requests_match"]
                and prefix_checks[key]["public_observations_match"]]

            def delta(key):
                return candidate[key]["net_arpu_gain"] - baseline[key]["net_arpu_gain"]

            comparisons.append({
                "family": family, "candidate": policy, "reference": "fixed_200",
                "attempted_pairs": len(keys), "valid_pairs": len(valid),
                "controlled_pairs": len(controlled),
                "invalid_or_missing_pairs": [key for key in keys if key not in valid],
                "protocol_mismatch_pairs": [key for key in valid if key not in controlled],
                "net_gain_difference": numeric_summary([delta(key) for key in valid]),
                "controlled_net_gain_difference": numeric_summary(
                    [delta(key) for key in controlled]),
                "wins": sum(delta(key) > 0 for key in valid),
                "ties": sum(delta(key) == 0 for key in valid),
                "paired_deltas": [{"model_seed": key[0], "noise_seed": key[1],
                                   "net_gain_difference": delta(key)} for key in valid],
                "common_prefix_checks": list(prefix_checks.values()),
            })
    return comparisons


def model_summaries(records: list[dict], policies: list[str]) -> list[dict]:
    from scripts.benchmark_agent import aggregate

    result = []
    identities = {(record["family"], record["model_seed"]) for record in records}
    for family, model_seed in sorted(identities, key=str):
        selected = [record for record in records
                    if record["family"] == family and record["model_seed"] == model_seed]
        result.append({"family": family, "model_seed": model_seed,
                       "policies": aggregate(selected, policies),
                       "paired_comparisons": family_comparisons(selected, policies)})
    return result


def changed_source_files(manifest: dict, root: Path, kit: Path, script_path: Path) -> list[str]:
    changed = []
    groups = (
        ("sources_sha256", root), ("harness_sources_sha256", root),
        ("public_tools_sha256", kit), ("public_data_sha256", kit),
    )
    for group, base in groups:
        for name, checksum in manifest[group].items():
            path = base / name
            if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != checksum:
                changed.append(f"{group}:{name}")
    if hashlib.sha256(script_path.read_bytes()).hexdigest() != manifest["diagnostic_source_sha256"]:
        changed.append("scripts/diagnose_pilots.py")
    return changed


def main():
    root = Path(__file__).resolve().parents[1]
    sys.path[:0] = [str(root), str(root / "packages")]
    from campaign_engine.candidates import load_history

    from scripts.benchmark_agent import (
        EngineBenchAgent,
        aggregate,
        evaluate_one,
        load_public_module,
        make_manifest,
        save_report,
    )

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--kit", type=Path, default=root / "data/participant-kit")
    parser.add_argument("--output", type=Path, default=root / "artifacts/pilot_diagnostics.json")
    parser.add_argument("--local-runs", type=int, default=10)
    parser.add_argument("--model-runs", type=int, default=2)
    parser.add_argument("--noise-runs", type=int, default=3)
    parser.add_argument("--include-screen1", action="store_true")
    args = parser.parse_args()
    if args.local_runs < 1 or args.model_runs < 1 or args.noise_runs < 1:
        parser.error("Run counts must be positive")
    kit = args.kit.resolve()
    args.output = args.output.resolve()
    options = policy_options(args.include_screen1)
    policies = list(options)
    manifest = make_manifest(root, kit, ["fixed_200", "adaptive_choice_200"], args.local_runs)
    manifest["engine_options"] = {name: asdict(option) for name, option in options.items()}
    manifest["diagnostic_source_sha256"] = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    manifest["synthetic_generator"] = {
        "version": GENERATOR_VERSION,
        "model_seeds": list(range(args.model_runs)),
        "noise_seeds": list(range(args.noise_runs)),
        "families": {
            "history_independent": "Change=-.35+1.10*u; conversion=.20+.65*v, keyed per arm.",
            "ranking_reversed": (
                "Descending observed history rank maps change from -.45 to .65 plus "
                ".10*(u-.5); unseen arms use history_independent ranges. Reverses historical "
                "ranking, because the Gaussian effect prior itself is zero."
            ),
            "sparse_positive": (
                "12% of arms have change=.35+.90*v; others=-.02-.25*v; "
                "conversion=.20+.65*w."
            ),
        },
        "randomness": (
            "SHA-256(version, family, model_seed, from, segment, target); fixed per model."
        ),
        "scope": "Effects are fixed within each run; models change between runs, not over time.",
    }
    report = {
        "format_version": 1, "complete": False, "scope": "Paired development diagnostics",
        "limitations": [
            "Same environment seeds do not produce identical observations after different actions.",
            "Synthetic models are declared stress cases; no holdout or confidence-interval claim.",
            "Only pairs with ten actual/requested SMS pilots of 200 isolate choice at this budget.",
            "Controlled pairs also require identical requests and full observations for screening.",
            "Family SD mixes model shifts and noise; use per-model SD for noise variability.",
        ],
        "manifest": manifest, "records": [], "summary_by_family": {}, "summary_by_model": [],
        "paired_comparisons": [],
    }
    save_report(args.output, report)
    sys.path.insert(0, str(kit))
    history = load_history(kit)
    profile = pd.read_csv(kit / "customer_profile.csv")
    tariffs = pd.read_csv(kit / "data/dict_tariff.csv")
    environment = load_public_module("redorda_diagnostic_environment", kit / "environment.py")
    scorer = load_public_module("redorda_diagnostic_scoring", kit / "scoring_core.py")
    evaluations = []
    with contextlib.chdir(kit):
        local = load_public_module("redorda_diagnostic_local_eval", kit / "local_eval.py")
        evaluations.append(("local", None, local, range(args.local_runs)))
        model_hashes = {}
        for family in SYNTHETIC_FAMILIES:
            for model_seed in range(args.model_runs):
                effects = synthetic_effects(tariffs, history, family, model_seed)
                model_hashes[f"{family}:{model_seed}"] = hashlib.sha256(
                    effects.to_json(orient="records", double_precision=15).encode("utf-8")
                ).hexdigest()
                evaluator = SyntheticEvaluator(profile, tariffs, effects, environment, scorer)
                evaluations.append((family, model_seed, evaluator, range(args.noise_runs)))
        manifest["synthetic_effects_sha256"] = model_hashes
        save_report(args.output, report)
        for family, model_seed, evaluator, seeds in evaluations:
            for seed in seeds:
                for policy, option in options.items():
                    delegate = EngineBenchAgent(option.policy, history)
                    delegate.options = option
                    record = evaluate_one(evaluator, delegate, policy, seed)
                    record.update({"family": family, "model_seed": model_seed})
                    record["pilot_protocol"] = pilot_protocol_flags(record)
                    report["records"].append(record)
                    report["summary_by_family"][family] = aggregate(
                        [row for row in report["records"] if row["family"] == family], policies,
                    )
                    report["paired_comparisons"] = family_comparisons(report["records"], policies)
                    report["summary_by_model"] = model_summaries(report["records"], policies)
                    save_report(args.output, report)
                    net = record["net_arpu_gain"]
                    net_text = f"{net:,.0f}" if isinstance(net, (int, float)) else "unavailable"
                    controlled = record["pilot_protocol"]["exact_ten_sms_pilots_of_200"]
                    print(f"{family} model={model_seed} seed={seed} {policy}: "
                          f"net={net_text} valid={record['valid']} 10x200={controlled}", flush=True)
    changed = changed_source_files(manifest, root, kit, Path(__file__))
    report.update({"complete": True, "source_files_changed_during_run": changed,
                   "all_runs_valid": all(record["valid"] for record in report["records"]),
                   "all_pilots_controlled": all(
                       record["pilot_protocol"]["exact_ten_sms_pilots_of_200"]
                       for record in report["records"]),
                   })
    save_report(args.output, report)
    print(f"Saved {args.output}")
    raise SystemExit(0 if report["all_runs_valid"] and not changed else 1)


if __name__ == "__main__":
    main()

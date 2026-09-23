"""Paired policy comparison through the local evaluator."""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import importlib.metadata
import importlib.util
import io
import json
import math
import os
import statistics
import sys
import time
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

POLICIES = ("adaptive", "fixed_100", "fixed_200", "wide_100", "evolve", "template")


def load_public_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise ValueError(f"Cannot load public tool: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


class RecordingAgent:
    """Record counters, campaigns, and runtime around Agent.act."""

    def __init__(self, delegate):
        self.delegate = delegate
        self.error = None
        self.validation = None
        self.campaigns = []
        self.pilot_observations = []
        self.public_resources = {}
        self.elapsed = None
        self.called = False

    def act(self, env):
        from scripts.run_official import validate_agent_result

        self.called = True
        started = time.monotonic()
        before = {
            "history": len(env.pilot_history),
            "budget": float(env.remaining_budget),
            "contacts": int(env.remaining_contacts),
            "pilots_left": env.pilots_left,
        }
        try:
            self.campaigns = self.delegate.act(env)
            self.validation = validate_agent_result(
                env, self.campaigns, before, time.monotonic() - started
            )
            return self.campaigns
        except Exception as exc:
            self.error = f"{type(exc).__name__}: {exc}"
            raise
        finally:
            self.elapsed = time.monotonic() - started
            self.pilot_observations = [dict(item) for item in env.pilot_history]
            self.public_resources = {
                "remaining_budget": float(env.remaining_budget),
                "remaining_contacts": int(env.remaining_contacts),
                "pilots_left": int(env.pilots_left),
            }


class EngineBenchAgent:
    def __init__(self, policy: str, history):
        from campaign_engine.engine_types import EngineOptions

        self.options = EngineOptions(policy=policy)
        self.history = history
        self.last_result = None

    def act(self, env):
        from campaign_engine.runner import run_campaigns

        self.last_result = run_campaigns(env, history=self.history, options=self.options)
        if self.last_result.status != "completed":
            raise RuntimeError(f"Engine {self.last_result.status}: {self.last_result.stop_reason}")
        return self.last_result.campaigns


def evaluate_one(evaluator, delegate, policy: str, seed: int) -> dict:
    agent = RecordingAgent(delegate)
    captured = io.StringIO()
    started = time.monotonic()
    score = None
    error = None
    with contextlib.redirect_stdout(captured), contextlib.redirect_stderr(captured):
        try:
            score = evaluator.evaluate_agent(agent, seed=seed, verbose=False)
        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}"
    error = error or agent.error
    if not agent.called:
        error = error or "Public evaluator did not call Agent.act"
    elif agent.validation is None:
        error = error or "Agent did not return a validated final portfolio"
    if not isinstance(score, dict):
        error = error or "Public evaluator returned no score"
        score = {}
    for key in ("net_arpu_gain", "total_cost", "total_contacts"):
        value = score.get(key)
        if not isinstance(value, (int, float)) or not math.isfinite(value):
            error = error or f"Official score is missing a finite {key}"
    if isinstance(score.get("total_cost"), (int, float)) and not 0 <= score["total_cost"] <= 100000:
        error = error or "Official total cost exceeds limits"
    if isinstance(score.get("total_contacts"), (int, float)) and not (
        0 <= score["total_contacts"] <= 15000
    ):
        error = error or "Official total contacts exceed limits"
    output = captured.getvalue()
    if "отброшена:" in output or "Агент упал:" in output:
        error = error or "Public evaluator reported a failed agent or discarded campaign"
    record = {
        "policy": policy,
        "seed": seed,
        "valid": error is None,
        "error": error,
        "runtime_seconds": time.monotonic() - started,
        "agent_runtime_seconds": agent.elapsed,
        "net_arpu_gain": score.get("net_arpu_gain"),
        "total_cost": score.get("total_cost"),
        "total_contacts": score.get("total_contacts"),
        "n_pilots": len(agent.pilot_observations),
        "n_final_campaigns": len(agent.campaigns) if isinstance(agent.campaigns, list) else 0,
        "campaigns": agent.campaigns,
        "campaigns_detail": score.get("campaigns_detail", []),
        "pilot_observations": agent.pilot_observations,
        "public_resources": agent.public_resources,
        "validation": agent.validation,
        "evaluator_output": output,
    }
    result = getattr(delegate, "last_result", None)
    if result is not None:
        record["engine_metadata"] = result.metadata
        record["engine_stop_reason"] = result.stop_reason
        record["pilot_requests"] = [pilot.request for pilot in result.pilots]
        record["warnings"] = result.warnings
        record["engine_estimates"] = result.estimates
    return record


def numeric_summary(values: list[float]) -> dict:
    if not values:
        return {"mean": None, "sample_sd": None, "min": None, "max": None}
    return {
        "mean": statistics.mean(values),
        "sample_sd": statistics.stdev(values) if len(values) > 1 else None,
        "min": min(values),
        "max": max(values),
    }


def aggregate(records: list[dict], policies: list[str]) -> dict:
    summary = {}
    for policy in policies:
        selected = [record for record in records if record["policy"] == policy]
        valid = [record for record in selected if record["valid"]]
        net = [record["net_arpu_gain"] for record in valid]
        summary[policy] = {
            "attempted_runs": len(selected),
            "valid_runs": len(valid),
            "invalid_runs": len(selected) - len(valid),
            "invalid_seeds": [record["seed"] for record in selected if not record["valid"]],
            "net_arpu_gain": numeric_summary(net),
            "negative_count": sum(value < 0 for value in net),
            "zero_count": sum(value == 0 for value in net),
            "positive_count": sum(value > 0 for value in net),
            "total_contacts": numeric_summary([record["total_contacts"] for record in valid]),
            "total_cost": numeric_summary([record["total_cost"] for record in valid]),
            "runtime_seconds": numeric_summary([record["runtime_seconds"] for record in selected]),
        }
    return summary


def paired_comparisons(records: list[dict], policies: list[str]) -> list[dict]:
    by_policy = {
        policy: {record["seed"]: record for record in records if record["policy"] == policy}
        for policy in policies
    }
    comparisons = []
    if "adaptive" not in policies:
        return comparisons
    candidate = by_policy["adaptive"]
    for reference in [policy for policy in policies if policy != "adaptive"]:
        baseline = by_policy[reference]
        all_seeds = sorted(set(candidate) | set(baseline))
        valid_seeds = [
            seed
            for seed in all_seeds
            if seed in candidate
            and seed in baseline
            and candidate[seed]["valid"]
            and baseline[seed]["valid"]
        ]
        deltas = [
            candidate[seed]["net_arpu_gain"] - baseline[seed]["net_arpu_gain"]
            for seed in valid_seeds
        ]
        comparisons.append(
            {
                "candidate": "adaptive",
                "reference": reference,
                "paired_runs": len(valid_seeds),
                "excluded_seeds": [seed for seed in all_seeds if seed not in valid_seeds],
                "net_gain_difference": numeric_summary(deltas),
                "wins": sum(delta > 0 for delta in deltas),
                "ties": sum(delta == 0 for delta in deltas),
            }
        )
    return comparisons


def json_compatible(value):
    """Keep failures visible while emitting strict JSON (never NaN/Infinity)."""
    if isinstance(value, dict):
        return {str(key): json_compatible(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_compatible(item) for item in value]
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if hasattr(value, "item"):
        return json_compatible(value.item())
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    # Invalid agent outputs must not prevent the failure record being written.
    return {"unserializable_type": type(value).__name__}


def save_report(path: Path, report: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(json_compatible(report), ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )


def capture_public_provenance(root: Path, kit: Path, *, stage: str = "run_start") -> dict:
    """Hash only the named public inputs; a post-run snapshot never rewrites run history."""
    if stage not in {"run_start", "post_run"}:
        raise ValueError("Provenance stage must be run_start or post_run")
    harness = ("scripts/benchmark_agent.py", "scripts/run_official.py")
    public_tools = (
        "local_eval.py",
        "agent_template.py",
        "scoring_core.py",
        "environment.py",
        "make_submission.py",
    )
    public_data = (
        "customer_profile.csv",
        "tariff_dictionary.csv",
        "data/dict_tariff.csv",
        "data/change_tariff.csv",
    )

    def checksums(base, names):
        return {name: hashlib.sha256((base / name).read_bytes()).hexdigest() for name in names}

    return {
        "provenance_captured_at": datetime.now(timezone.utc).isoformat(),
        "provenance_capture_stage": stage,
        "harness_sources_sha256": checksums(root, harness),
        "public_tools_sha256": checksums(kit, public_tools),
        "public_data_sha256": checksums(kit, public_data),
    }


def make_manifest(root: Path, kit: Path, policies: list[str], runs: int) -> dict:
    from campaign_engine.contracts import RunConfig
    from campaign_engine.engine_types import EngineOptions

    sources = sorted((root / "packages/campaign_engine").glob("*.py"))
    return {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "environment_seeds": list(range(runs)),
        "engine_config": RunConfig().model_dump(),
        "engine_options": {
            policy: asdict(EngineOptions(policy=policy))
            for policy in policies
            if policy != "template"
        },
        "sources_sha256": {
            str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in sources
        },
        **capture_public_provenance(root, kit),
        "dependencies": {
            name: importlib.metadata.version(name)
            for name in ("numpy", "pandas", "pydantic", "openai")
        },
    }


def main():
    root = Path(__file__).resolve().parents[1]
    sys.path[:0] = [str(root), str(root / "packages")]
    from campaign_engine.candidates import load_history
    from dotenv import load_dotenv

    load_dotenv(root / ".env")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runs", type=int, default=10)
    parser.add_argument(
        "--policies", nargs="+", choices=POLICIES, default=["adaptive", "fixed_100", "template"]
    )
    parser.add_argument("--output", type=Path, default=root / "artifacts/benchmark.json")
    args = parser.parse_args()
    if args.runs < 1:
        parser.error("--runs must be positive")
    if len(set(args.policies)) != len(args.policies):
        parser.error("--policies must not contain duplicates")
    kit = Path(os.environ.get("PARTICIPANT_KIT_DIR", "data/participant-kit"))
    if not kit.is_absolute():
        kit = root / kit
    kit = kit.resolve()
    required = (
        "local_eval.py",
        "agent_template.py",
        "scoring_core.py",
        "environment.py",
        "make_submission.py",
        "customer_profile.csv",
        "tariff_dictionary.csv",
        "data/dict_tariff.csv",
        "data/change_tariff.csv",
    )
    if any(not (kit / name).is_file() for name in required):
        parser.error("Import the participant ZIP before running this benchmark")
    output_path = args.output.resolve()
    sys.path.insert(0, str(kit))
    report = {
        "format_version": 1,
        "status": "preliminary",
        "complete": False,
        "scope": "Local smoke comparison on the mock environment",
        "limitations": [
            "Confidence intervals are not calculated in this smoke report.",
            "Numerical summaries use valid runs only; every invalid run remains in records.",
            "Paired environment seeds do not guarantee identical noise after different actions.",
            "Each policy receives a fresh environment; the engine's default seed remains 42.",
        ],
        "manifest": make_manifest(root, kit, args.policies, args.runs),
        "records": [],
        "summary": {},
        "paired_comparisons": [],
    }
    save_report(output_path, report)
    with contextlib.chdir(kit):
        evaluator = load_public_module("redorda_benchmark_public_eval", kit / "local_eval.py")
        template = load_public_module("redorda_benchmark_template", kit / "agent_template.py")
        history = load_history(kit)
        for seed in range(args.runs):
            for policy in args.policies:
                delegate = (
                    template.Agent() if policy == "template" else EngineBenchAgent(policy, history)
                )
                record = evaluate_one(evaluator, delegate, policy, seed)
                report["records"].append(record)
                report["summary"] = aggregate(report["records"], args.policies)
                report["paired_comparisons"] = paired_comparisons(report["records"], args.policies)
                save_report(output_path, report)
                net = record["net_arpu_gain"]
                net_text = f"{net:,.0f}" if isinstance(net, (int, float)) else "unavailable"
                status = "valid" if record["valid"] else "INVALID"
                print(
                    f"{policy:10s} seed={seed:3d} net={net_text:>12s} "
                    f"{status} {record['runtime_seconds']:.2f}s",
                    flush=True,
                )
    report["complete"] = True
    report["all_runs_valid"] = all(record["valid"] for record in report["records"])
    save_report(output_path, report)
    print(f"Preliminary report: {output_path}")
    for policy, summary in report["summary"].items():
        net = summary["net_arpu_gain"]
        mean = f"{net['mean']:,.0f}" if net["mean"] is not None else "unavailable"
        sd = f"{net['sample_sd']:,.0f}" if net["sample_sd"] is not None else "unavailable"
        print(
            f"{policy}: mean={mean}, sample SD={sd}, negative={summary['negative_count']}, "
            f"invalid={summary['invalid_runs']}/{summary['attempted_runs']}"
        )
    raise SystemExit(0 if report["all_runs_valid"] else 1)


if __name__ == "__main__":
    main()

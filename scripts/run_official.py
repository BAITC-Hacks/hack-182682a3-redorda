"""Run evaluation and CSV generation with the repository's Agent entry point."""

import argparse
import csv
import importlib.util
import math
import os
import runpy
import shutil
import subprocess
import sys
import time
from pathlib import Path

CAMPAIGN_COLUMNS = (
    "campaign_name",
    "filter_arpu_segment",
    "filter_data_segment",
    "filter_call_segment",
    "filter_current_tariff",
    "target_tariff",
    "channel",
)
VALIDATION_MARKER = "[RedOrda validation]"


def expected_runs(command: str, forwarded: list[str]) -> int:
    """Resolve the number of evaluation runs."""
    runs = 1
    if command == "local_eval" and "--runs" in forwarded:
        try:
            runs = int(forwarded[forwarded.index("--runs") + 1])
        except (ValueError, IndexError) as exc:
            raise ValueError("--runs must be a positive integer") from exc
    if runs < 1:
        raise ValueError("--runs must be a positive integer")
    return runs


def validate_agent_result(env, campaigns, before: dict, elapsed: float) -> dict:
    """Validate campaign output, pilot accounting, and resource limits."""
    import pandas as pd
    from scoring_core import apply_filters, validate_strategy

    if not isinstance(campaigns, list) or not 1 <= len(campaigns) <= 10:
        raise ValueError("Agent must return 1–10 final campaigns")
    for campaign in campaigns:
        if not isinstance(campaign, dict) or set(campaign) - set(CAMPAIGN_COLUMNS):
            raise ValueError("Final campaigns must use only the seven submission columns")
    validate_strategy(pd.DataFrame(campaigns), env.tariffs)
    pilots = env.pilot_history[before["history"] :]
    if not 1 <= len(pilots) <= 20:
        raise ValueError("Agent must complete at least one successful pilot (maximum 20)")
    budget = float(env.remaining_budget)
    contacts = int(env.remaining_contacts)
    if not math.isfinite(budget) or not 0 <= budget <= before["budget"] <= 100_000:
        raise ValueError("Pilot budget is outside the official limits")
    if not 0 <= contacts <= before["contacts"] <= 15_000:
        raise ValueError("Pilot contacts are outside the official limits")
    if not 0 <= env.pilots_left <= before["pilots_left"] <= 20:
        raise ValueError("Pilot count is outside the official limits")
    if before["pilots_left"] - env.pilots_left != len(pilots):
        raise ValueError("Pilot history and remaining pilot count disagree")
    if any(not 1 <= int(pilot["n_customers"]) <= 200 for pilot in pilots):
        raise ValueError("Actual pilot size is outside the official limits")
    pilot_contacts = sum(int(pilot["n_customers"]) for pilot in pilots)
    pilot_cost = sum(float(pilot["cost"]) for pilot in pilots)
    if pilot_contacts != before["contacts"] - contacts or not math.isclose(
        pilot_cost, before["budget"] - budget, abs_tol=1e-6
    ):
        raise ValueError("Public pilot expenses disagree with remaining resources")
    if elapsed > 600:
        raise ValueError("Agent exceeded the 600-second runtime limit")

    final_contacts = 0
    final_cost = 0.0
    for campaign in campaigns:
        audience = apply_filters(env.customer_profile, pd.Series(campaign))
        unit_cost = float(env.channels[campaign["channel"]]["cost_per_contact"])
        count = min(len(audience), 5_000, contacts)
        if unit_cost > 0:
            count = min(count, int(budget // unit_cost))
        if count < 1:
            raise ValueError("A final campaign has no reachable audience after resource limits")
        cost = count * unit_cost
        contacts -= count
        budget -= cost
        final_contacts += count
        final_cost += cost
    return {
        "campaigns": len(campaigns),
        "pilots": len(pilots),
        "contacts": pilot_contacts + final_contacts,
        "cost": pilot_cost + final_cost,
    }


def validate_submission(path: Path, campaigns: list[dict]) -> None:
    with path.open(newline="", encoding="utf-8") as stream:
        reader = csv.DictReader(stream)
        if reader.fieldnames != list(CAMPAIGN_COLUMNS):
            raise ValueError("submission.csv does not contain the seven official columns")
        rows = list(reader)
    expected = [
        {column: str(campaign.get(column) or "") for column in CAMPAIGN_COLUMNS}
        for campaign in campaigns
    ]
    if rows != expected:
        raise ValueError("submission.csv differs from the campaigns returned by Agent.act")


def bootstrap(root: Path, kit: Path, command: str, forwarded: list[str]) -> int:
    """Register the root Agent module before executing the selected script."""
    sys.path[:0] = [str(root / "packages"), str(root), str(kit)]
    entrypoint = root / "agent.py"
    spec = importlib.util.spec_from_file_location("agent", entrypoint)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot import Agent from {entrypoint}")
    module = importlib.util.module_from_spec(spec)
    sys.modules["agent"] = module
    spec.loader.exec_module(module)
    if Path(module.__file__).resolve() != entrypoint.resolve():
        raise RuntimeError("Unexpected Agent entry point")
    original_agent = module.Agent
    reports = []
    failures = []
    outputs = []

    class VerifiedAgent(original_agent):
        def act(self, env):
            started = time.monotonic()
            before = {
                "history": len(env.pilot_history),
                "budget": float(env.remaining_budget),
                "contacts": int(env.remaining_contacts),
                "pilots_left": env.pilots_left,
            }
            try:
                campaigns = super().act(env)
                report = validate_agent_result(env, campaigns, before, time.monotonic() - started)
                reports.append(report)
                outputs.append(campaigns)
                return campaigns
            except Exception as exc:
                failures.append(f"{type(exc).__name__}: {exc}")
                raise

    module.Agent = VerifiedAgent
    sys.argv = [str(kit / f"{command}.py"), *forwarded]
    print(f"{VALIDATION_MARKER} Agent entry point: {entrypoint}", flush=True)
    try:
        runpy.run_path(sys.argv[0], run_name="__main__")
    except SystemExit as exc:
        if exc.code not in (None, 0):
            failures.append(f"Official script exited with {exc.code}")
    if len(reports) != expected_runs(command, forwarded):
        failures.append("Not all requested runs returned valid final campaigns")
    if failures:
        for failure in failures:
            print(f"{VALIDATION_MARKER} FAILED: {failure}", file=sys.stderr)
        return 1
    if command == "make_submission":
        validate_submission(kit / "submission.csv", outputs[0])
    for number, report in enumerate(reports, 1):
        print(
            f"{VALIDATION_MARKER} run {number}: "
            f"{report['campaigns']} campaigns, {report['pilots']} successful pilots, "
            f"{report['contacts']} contacts, cost {report['cost']:.2f} — OK"
        )
    return 0


def run_tool(root: Path, kit: Path, command: str, forwarded: list[str]):
    """Run from the dataset directory without shadowing the root Agent module."""
    env = os.environ.copy()
    env["PARTICIPANT_KIT_DIR"] = str(kit)
    return subprocess.run(
        [
            sys.executable,
            str(Path(__file__).resolve()),
            "--_bootstrap",
            str(root),
            str(kit),
            command,
            *forwarded,
        ],
        cwd=kit,
        env=env,
        capture_output=True,
        text=True,
        timeout=620 * expected_runs(command, forwarded) + 30,
    )


def main():
    if len(sys.argv) > 1 and sys.argv[1] == "--_bootstrap":
        raise SystemExit(bootstrap(Path(sys.argv[2]), Path(sys.argv[3]), sys.argv[4], sys.argv[5:]))
    from dotenv import load_dotenv

    root = Path(__file__).resolve().parents[1]
    load_dotenv(root / ".env")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["local_eval", "make_submission"])
    args, forwarded = parser.parse_known_args()
    kit = Path(os.getenv("PARTICIPANT_KIT_DIR") or "data/participant-kit")
    if not kit.is_absolute():
        kit = root / kit
    kit = kit.resolve()
    if not (kit / f"{args.command}.py").is_file():
        parser.error("Import the participant ZIP first; see README.md")
    try:
        completed = run_tool(root, kit, args.command, forwarded)
    except ValueError as exc:
        parser.error(str(exc))
    except subprocess.TimeoutExpired:
        parser.exit(1, "Official evaluation exceeded its runtime limit\n")
    print(completed.stdout, end="")
    print(completed.stderr, end="", file=sys.stderr)
    failed_output = any(
        marker in completed.stdout + completed.stderr
        for marker in ("Агент упал:", "отброшена:", "Traceback (most recent call last)")
    )
    returncode = completed.returncode or int(failed_output)
    if returncode == 0 and args.command == "make_submission":
        (root / "artifacts").mkdir(exist_ok=True)
        shutil.copyfile(kit / "submission.csv", root / "artifacts/submission.csv")
        print("Saved artifacts/submission.csv")
    raise SystemExit(returncode)


if __name__ == "__main__":
    main()

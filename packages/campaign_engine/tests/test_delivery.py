"""Delivery must run the root strategy and survive outside the source checkout."""

import ast
import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from scripts.build_agent import BundleError, build, render_bundle
from scripts.run_official import expected_runs, run_tool

ROOT = Path(__file__).resolve().parents[3]


def write_source(path, source):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(source), encoding="utf-8")


@pytest.fixture
def public_tool_fixture(tmp_path):
    root = tmp_path / "repository"
    kit = tmp_path / "kit"
    write_source(kit / "agent.py", 'raise RuntimeError("WRONG KIT AGENT IMPORTED")')
    write_source(
        kit / "scoring_core.py",
        """
        def validate_strategy(frame, tariffs):
            assert set(frame.target_tariff) <= set(tariffs.tariff_plan_code)
            assert set(frame.channel) <= {"sms", "push"}
        def apply_filters(profile, campaign):
            return profile
    """,
    )
    write_source(
        kit / "local_eval.py",
        """
        import pandas as pd
        from types import SimpleNamespace
        from agent import Agent
        env = SimpleNamespace(
            customer_profile=pd.DataFrame({"ID_NUMBER": range(20)}),
            tariffs=pd.DataFrame({"tariff_plan_code": ["tariff_1", "tariff_2"]}),
            channels={"sms": {"cost_per_contact": 4}, "push": {"cost_per_contact": 0}},
            remaining_budget=100000, remaining_contacts=15000, pilots_left=20,
            pilot_history=[],
        )
        try:
            Agent().act(env)
        except Exception as error:
            print("caught by public evaluator:", error)
    """,
    )
    root.mkdir()
    return root, kit


VALID_ACT = """
    class Agent:
        def act(self, env):
            env.pilot_history.append({"n_customers": 10, "cost": 40})
            env.remaining_budget -= 40
            env.remaining_contacts -= 10
            env.pilots_left -= 1
            print("ROOT_AGENT_USED")
            return [{"campaign_name": "test", "target_tariff": "tariff_2", "channel": "sms"}]
"""


def test_wrapper_imports_root_even_when_kit_contains_agent(public_tool_fixture):
    root, kit = public_tool_fixture
    write_source(root / "agent.py", VALID_ACT)
    completed = run_tool(root, kit, "local_eval", [])
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert "ROOT_AGENT_USED" in completed.stdout
    assert "WRONG KIT AGENT" not in completed.stdout + completed.stderr
    assert "1 campaigns, 1 successful pilots, 30 contacts" in completed.stdout


@pytest.mark.parametrize("configured_path", [None, "", "relative", "absolute"])
def test_cli_resolves_kit_from_repository_independently_of_cwd(
    public_tool_fixture, tmp_path, configured_path,
):
    root, kit = public_tool_fixture
    installed = root / "data/participant-kit"
    installed.parent.mkdir()
    kit.rename(installed)
    write_source(root / "agent.py", VALID_ACT)
    script = root / "scripts/run_official.py"
    write_source(script, (ROOT / "scripts/run_official.py").read_text())
    env = os.environ.copy()
    for name in ("PARTICIPANT_KIT_DIR", "PYTHONPATH", "OPENAI_API_KEY"):
        env.pop(name, None)
    env["PYTHON_DOTENV_DISABLED"] = "1"
    if configured_path is not None:
        env["PARTICIPANT_KIT_DIR"] = {
            "": "", "relative": "data/participant-kit", "absolute": str(installed),
        }[configured_path]

    completed = subprocess.run(
        [sys.executable, str(script), "local_eval", "--runs", "1"],
        cwd=tmp_path, env=env, text=True, capture_output=True, timeout=30,
    )

    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert f"Agent entry point: {root / 'agent.py'}" in completed.stdout
    assert "ROOT_AGENT_USED" in completed.stdout
    assert "WRONG KIT AGENT" not in completed.stdout + completed.stderr
    assert "1 campaigns, 1 successful pilots, 30 contacts" in completed.stdout


@pytest.mark.parametrize(
    "body, message",
    [
        ("return []", "1–10 final campaigns"),
        ("raise RuntimeError('broken strategy')", "broken strategy"),
        ("return [{'target_tariff': 'tariff_2', 'channel': 'sms'}]", "successful pilot"),
        (
            "return [{'target_tariff': 'tariff_2', 'channel': 'sms', 'explicit_ids': [1]}]",
            "seven submission columns",
        ),
    ],
)
def test_wrapper_rejects_failure_swallowed_by_official_script(public_tool_fixture, body, message):
    root, kit = public_tool_fixture
    write_source(root / "agent.py", f"class Agent:\n    def act(self, env):\n        {body}\n")
    completed = run_tool(root, kit, "local_eval", [])
    assert completed.returncode != 0
    assert message in completed.stdout + completed.stderr
    assert "Not all requested runs" in completed.stderr


@pytest.mark.parametrize("arguments", [["--runs", "0"], ["--runs", "x"], ["--runs"]])
def test_wrapper_requires_positive_run_count(arguments):
    with pytest.raises(ValueError, match="positive integer"):
        expected_runs("local_eval", arguments)


def test_bundle_detects_global_collisions(tmp_path):
    write_source(tmp_path / "shared.py", "COLLISION = 1")
    write_source(
        tmp_path / "agent.py",
        """
        from campaign_engine.shared import COLLISION
        COLLISION = 2
        class Agent:
            pass
    """,
    )
    with pytest.raises(BundleError, match="Global name collision.*COLLISION"):
        render_bundle(tmp_path)


def test_bundle_detects_cycles(tmp_path):
    write_source(tmp_path / "shared.py", "from campaign_engine.agent import Agent")
    write_source(tmp_path / "agent.py", "from campaign_engine.shared import Agent")
    with pytest.raises(BundleError, match="Circular local imports"):
        render_bundle(tmp_path)


def test_bundle_orders_dependencies_without_runtime_code_execution(tmp_path):
    write_source(
        tmp_path / "shared.py",
        """
        from dataclasses import dataclass
        @dataclass
        class Value:
            number: int = 3
    """,
    )
    write_source(
        tmp_path / "agent.py",
        """
        from dataclasses import dataclass
        from campaign_engine.shared import Value
        class Agent:
            def act(self, env):
                return Value().number
    """,
    )
    source, manifest, dependencies = render_bundle(tmp_path)
    assert list(manifest["sources"]) == ["campaign_engine.shared", "campaign_engine.agent"]
    assert not dependencies
    tree = ast.parse(source)
    assert not any(
        isinstance(node, ast.ImportFrom) and (node.module or "").startswith("campaign_engine")
        for node in ast.walk(tree)
    )
    assert not any(
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id in {"eval", "exec", "compile", "__import__"}
        for node in ast.walk(tree)
    )
    compile(source, "standalone-agent.py", "exec")


OFFLINE_HARNESS = """
import importlib.abc
import importlib.util
import json
import os
import sys
from pathlib import Path
import pandas as pd

os.environ["PARTICIPANT_KIT_DIR"] = str(Path.cwd() / "missing_history")
if sys.argv[1] == "source":
    sys.path.insert(0, sys.argv[2])
    from campaign_engine.agent import Agent
else:
    class NoRepositoryImports(importlib.abc.MetaPathFinder):
        def find_spec(self, fullname, path=None, target=None):
            if fullname == "campaign_engine" or fullname.startswith("campaign_engine."):
                raise ImportError("Standalone artifact attempted a repository import")
    sys.meta_path.insert(0, NoRepositoryImports())
    spec = importlib.util.spec_from_file_location("agent", sys.argv[2])
    module = importlib.util.module_from_spec(spec)
    sys.modules["agent"] = module
    spec.loader.exec_module(module)
    Agent = module.Agent

class PublicEnvironment:
    def __init__(self):
        self.customer_profile = pd.DataFrame({
            "ID_NUMBER": range(120), "predicted_arpu": [6500.] * 120,
            "current_tariff": ["tariff_1"] * 80 + ["tariff_2"] * 40,
            "arpu_segment": ["HIGH"] * 120,
            "data_segment": ["HEAVY"] * 120, "call_segment": ["MEDIUM"] * 120,
        })
        self.tariffs = pd.DataFrame({"tariff_plan_code": ["tariff_1", "tariff_2", "tariff_3"]})
        self.channels = {
            name: {"cost_per_contact": cost, "conversion_multiplier": multiplier}
            for name, cost, multiplier in [("push", 0, .5), ("sms", 4, .65),
                                          ("digital_ads", 22, .85), ("call", 160, 1.2)]
        }
        self.remaining_budget = 100000
        self.remaining_contacts = 15000
        self.pilots_left = 20
        self.pilot_history = []
        self.calls = []
    def run_pilot(self, **request):
        audience = self.customer_profile
        for field in ("current_tariff", "arpu_segment", "data_segment", "call_segment"):
            value = request.get("filter_" + field)
            if value is not None:
                audience = audience[audience[field].isin(str(value).split(";"))]
        count = min(int(request.get("n_customers", 100)), len(audience), self.remaining_contacts)
        cost = self.channels[request["channel"]]["cost_per_contact"]
        if cost:
            count = min(count, int(self.remaining_budget // cost))
        assert count > 0 and self.pilots_left > 0
        self.remaining_budget -= count * cost
        self.remaining_contacts -= count
        self.pilots_left -= 1
        ratio = .18 if request["target_tariff"] == "tariff_3" else .09
        observation = {
            "pilot": "pilot_" + str(len(self.pilot_history) + 1),
            "n_customers": count, "cost": count * cost,
            "target_tariff": request["target_tariff"], "channel": request["channel"],
            "observed_lift_ratio": ratio,
            "observed_lift_total": ratio * float(audience.iloc[:count].predicted_arpu.sum()),
            "remaining_budget": self.remaining_budget,
            "remaining_contacts": self.remaining_contacts,
        }
        self.calls.append(request)
        self.pilot_history.append(observation)
        return observation

env = PublicEnvironment()
campaigns = Agent().act(env)
assert 1 <= len(campaigns) <= 10 and 1 <= len(env.calls) <= 20
print(json.dumps({"campaigns": campaigns, "pilots": env.calls}, sort_keys=True))
"""


def test_standalone_agent_matches_source_offline_without_repository_imports(tmp_path):
    output = tmp_path / "delivery"
    manifest = build(ROOT, output)
    write_source(tmp_path / "offline_harness.py", OFFLINE_HARNESS)
    env = os.environ.copy()
    env.pop("PYTHONPATH", None)
    env.pop("OPENAI_API_KEY", None)
    observed = []
    for mode, entry in [("source", ROOT / "packages"), ("bundle", output / "agent.py")]:
        completed = subprocess.run(
            [sys.executable, "-I", str(tmp_path / "offline_harness.py"), mode, str(entry)],
            cwd=tmp_path,
            env=env,
            text=True,
            capture_output=True,
            timeout=30,
        )
        assert completed.returncode == 0, completed.stdout + completed.stderr
        observed.append(json.loads(completed.stdout.strip().splitlines()[-1]))
    assert observed[0] == observed[1]
    requirements = (output / "requirements.txt").read_text()
    assert "-e" not in requirements and "Django" not in requirements
    assert "campaign_engine.agent" in manifest["sources"]


def test_benchmark_retains_score_when_public_evaluator_swallows_agent_failure():
    from types import SimpleNamespace

    from scripts.benchmark_agent import evaluate_one

    class BrokenAgent:
        def act(self, env):
            env.pilot_history.append({"n_customers": 10, "cost": 40})
            env.remaining_budget -= 40
            env.remaining_contacts -= 10
            env.pilots_left -= 1
            raise RuntimeError("failure after a real pilot")

    class CatchingEvaluator:
        @staticmethod
        def evaluate_agent(agent, seed, verbose):
            env = SimpleNamespace(
                pilot_history=[],
                remaining_budget=100000,
                remaining_contacts=15000,
                pilots_left=20,
            )
            try:
                agent.act(env)
            except RuntimeError:
                pass
            return {"net_arpu_gain": 123.0, "total_cost": 40.0, "total_contacts": 10}

    record = evaluate_one(CatchingEvaluator, BrokenAgent(), "adaptive", 3)
    assert not record["valid"]
    assert record["net_arpu_gain"] == 123.0
    assert record["n_pilots"] == 1
    assert "failure after a real pilot" in record["error"]


def test_benchmark_reports_invalid_runs_and_excluded_pairs_explicitly():
    from scripts.benchmark_agent import aggregate, paired_comparisons

    records = [
        {
            "policy": policy,
            "seed": seed,
            "valid": valid,
            "net_arpu_gain": net,
            "total_cost": 100,
            "total_contacts": 25,
            "runtime_seconds": 1.0,
        }
        for policy, seed, valid, net in [
            ("adaptive", 0, True, 10),
            ("adaptive", 1, True, 20),
            ("fixed_100", 0, True, 8),
            ("fixed_100", 1, False, 99999),
        ]
    ]
    summary = aggregate(records, ["adaptive", "fixed_100"])
    assert summary["fixed_100"]["invalid_seeds"] == [1]
    assert summary["fixed_100"]["net_arpu_gain"]["mean"] == 8
    assert summary["fixed_100"]["net_arpu_gain"]["sample_sd"] is None
    comparisons = paired_comparisons(records, ["adaptive", "fixed_100"])
    assert comparisons[0]["paired_runs"] == 1
    assert comparisons[0]["excluded_seeds"] == [1]
    assert comparisons[0]["net_gain_difference"]["mean"] == 2


def test_standalone_history_uses_agent_location_and_explicit_kit(tmp_path):
    output = tmp_path / "delivery"
    build(ROOT, output)
    header = "AVG_ARPU_PREV_3M,AVG_ARPU_NEXT_3M,tariff_plan_code_from,tariff_plan_code_to\n"
    adjacent = output / "data/change_tariff.csv"
    override = tmp_path / "explicit-kit/data/change_tariff.csv"
    unrelated = tmp_path / "unrelated-cwd/data/change_tariff.csv"
    for path, value in [(adjacent, 100), (override, 200), (unrelated, 999)]:
        write_source(path, header + f"{value},400,tariff_1,tariff_2\n")
    script = tmp_path / "check_history.py"
    write_source(
        script,
        """
        import importlib.abc
        import importlib.util
        import json
        import os
        import sys
        class NoRepositoryImports(importlib.abc.MetaPathFinder):
            def find_spec(self, fullname, path=None, target=None):
                if fullname == "campaign_engine" or fullname.startswith("campaign_engine."):
                    raise ImportError("Unexpected repository import")
        sys.meta_path.insert(0, NoRepositoryImports())
        spec = importlib.util.spec_from_file_location("agent", sys.argv[1])
        module = importlib.util.module_from_spec(spec)
        sys.modules["agent"] = module
        spec.loader.exec_module(module)
        adjacent = module.load_history()
        os.environ["PARTICIPANT_KIT_DIR"] = sys.argv[2]
        override = module.load_history()
        print(json.dumps([
            [adjacent.attrs["public_source"], int(adjacent.AVG_ARPU_PREV_3M.iloc[0])],
            [override.attrs["public_source"], int(override.AVG_ARPU_PREV_3M.iloc[0])],
        ]))
    """,
    )
    env = os.environ.copy()
    for key in ("PARTICIPANT_KIT_DIR", "PYTHONPATH", "OPENAI_API_KEY"):
        env.pop(key, None)
    completed = subprocess.run(
        [sys.executable, "-I", str(script), str(output / "agent.py"), str(override.parents[1])],
        cwd=unrelated.parents[1],
        env=env,
        text=True,
        capture_output=True,
        timeout=30,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert json.loads(completed.stdout) == [[str(adjacent), 100], [str(override), 200]]


def test_benchmark_manifest_hashes_all_engine_modules_without_reading_private_data(tmp_path):
    import hashlib

    from scripts.benchmark_agent import make_manifest

    kit = tmp_path / "kit"
    tools = {
        "local_eval.py",
        "agent_template.py",
        "scoring_core.py",
        "environment.py",
        "make_submission.py",
    }
    data = {
        "customer_profile.csv",
        "tariff_dictionary.csv",
        "data/dict_tariff.csv",
        "data/change_tariff.csv",
    }
    for name in tools | data:
        write_source(kit / name, "# Public fixture, never executed\n")
    manifest = make_manifest(ROOT, kit, ["adaptive", "template"], 1)
    expected = {
        str(path.relative_to(ROOT)) for path in (ROOT / "packages/campaign_engine").glob("*.py")
    }
    assert set(manifest["sources_sha256"]) == expected
    assert "packages/campaign_engine/engine_types.py" in expected
    assert "packages/campaign_engine/pilots.py" in expected
    assert set(manifest["public_tools_sha256"]) == tools
    assert set(manifest["public_data_sha256"]) == data
    for name in data:
        assert (
            manifest["public_data_sha256"][name]
            == hashlib.sha256((kit / name).read_bytes()).hexdigest()
        )
    assert set(manifest["harness_sources_sha256"]) == {
        "scripts/benchmark_agent.py",
        "scripts/run_official.py",
    }
    assert manifest["provenance_capture_stage"] == "run_start"
    assert manifest["provenance_captured_at"].endswith("+00:00")


def test_benchmark_post_run_provenance_is_explicit_and_rejects_unknown_stage(tmp_path):
    from scripts.benchmark_agent import capture_public_provenance

    root = tmp_path / "source"
    kit = tmp_path / "kit"
    for name in ("scripts/benchmark_agent.py", "scripts/run_official.py"):
        write_source(root / name, "# Harness fixture\n")
    for name in (
        "local_eval.py",
        "agent_template.py",
        "scoring_core.py",
        "environment.py",
        "make_submission.py",
        "customer_profile.csv",
        "tariff_dictionary.csv",
        "data/dict_tariff.csv",
        "data/change_tariff.csv",
    ):
        write_source(kit / name, "# Public fixture\n")
    snapshot = capture_public_provenance(root, kit, stage="post_run")
    assert snapshot["provenance_capture_stage"] == "post_run"
    assert "sources_sha256" not in snapshot  # Does not replace historical algorithm hashes.
    assert "actual_archive_sha256" not in snapshot
    with pytest.raises(ValueError, match="Provenance stage"):
        capture_public_provenance(root, kit, stage="assumed_start")

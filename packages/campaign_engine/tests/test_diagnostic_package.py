import json
from zipfile import ZipFile

import pytest

from scripts import package_diagnostics as package


def test_whitelist_excludes_environment_data_replays_and_unrelated_files():
    required = [*package.SOURCE_FILES, "packages/campaign_engine/engine_types.py"]
    extra = [
        "packages/campaign_engine/agent.py",
        "packages/campaign_engine/tests/test_runner.py",
        ".env",
        ".env.example",
        "data/participant-kit/mock_environment.py",
        "artifacts/openai_hypotheses_replay.json",
        "backend/config/settings.py",
        "packages/campaign_engine/__pycache__/agent.cpython-312.pyc",
        "packages/campaign_engine/tests/.env",
        "packages/campaign_engine/.secret",
    ]
    selected = package.selected_source_paths(required + extra)
    assert "packages/campaign_engine/agent.py" in selected
    assert "packages/campaign_engine/tests/test_runner.py" in selected
    assert not set(extra[2:]) & set(selected)


def test_new_diagnostic_tests_include_their_scripts_without_breaking_old_snapshots():
    required = [*package.SOURCE_FILES, "packages/campaign_engine/engine_types.py"]
    old_snapshot = package.selected_source_paths(required)
    assert not set(package.OPTIONAL_SOURCE_FILES) & set(old_snapshot)
    scripts = [
        "scripts/diagnose_pilots.py",
        "scripts/diagnose_portfolio.py",
        "scripts/package_diagnostics.py",
    ]
    tests = [
        "packages/campaign_engine/tests/test_pilot_diagnostics.py",
        "packages/campaign_engine/tests/test_portfolio_diagnostics.py",
        "packages/campaign_engine/tests/test_diagnostic_package.py",
    ]
    new_snapshot = package.selected_source_paths(required + scripts + tests)
    assert set(scripts + tests) <= set(new_snapshot)


@pytest.fixture
def snapshot_fixture(tmp_path, monkeypatch):
    sources = {name: b"# Committed source\n" for name in package.SOURCE_FILES}
    sources["agent.py"] = b"COMMITTED_MARKER = True\n"
    sources["packages/campaign_engine/engine_types.py"] = (
        b'class EngineOptions:\n    policy: str = "adaptive"\n'
    )
    sources["requirements.lock"] = "\n".join(
        name + "==1.2.3" for name in package.DIAGNOSTIC_DEPENDENCIES
    ).encode()
    monkeypatch.setattr(
        package,
        "snapshot_sources",
        lambda root, reference: ("9711206" + "0" * 33, 1790150000, sources),
    )
    root = tmp_path / "repository"
    (root / "artifacts").mkdir(parents=True)
    (root / "agent.py").write_text("UNCOMMITTED_MARKER = True\n")
    (root / ".env").write_text("SECRET_MARKER=do-not-copy\n")
    (root / "artifacts/openai_hypotheses_replay.json").write_text("REPLAY_MARKER")
    for name in ("submission.csv", "agent-validation.md"):
        (root / "artifacts" / name).write_text("Existing diagnostic artifact\n")
    report = {
        "status": "preliminary",
        "complete": True,
        "records": [{"seed": 0}],
        "summary": {"adaptive": {"valid_runs": 1}},
        "manifest": {
            "sources_sha256": {"agent.py": "historical-sha"},
            "environment_seeds": [0],
        },
    }
    (root / "artifacts/benchmark.json").write_text(json.dumps(report))
    return root, sources


def test_archive_is_reproducible_commit_snapshot_with_honest_historical_provenance(
    tmp_path,
    snapshot_fixture,
):
    root, sources = snapshot_fixture
    first, second = tmp_path / "first.zip", tmp_path / "second.zip"
    manifest = package.build_archive(root, first)
    package.build_archive(root, second)
    assert first.read_bytes() == second.read_bytes()
    assert manifest["default_policy"] == "adaptive"
    assert manifest["missing_optional_artifacts"] == ["benchmark_evolve_smoke.json"]
    report = manifest["report_provenance"]["artifacts/benchmark.json"]
    assert report["source_hash_mismatches"] == ["agent.py"]
    assert not report["exact_source_bytes_confirmed"]
    with ZipFile(first) as archive:
        assert archive.read("agent.py") == sources["agent.py"]
        assert (
            archive.read("artifacts/benchmark.json")
            == (root / "artifacts/benchmark.json").read_bytes()
        )
        assert ".env" not in archive.namelist()
        assert not any("replay" in name for name in archive.namelist())
        content = b"\n".join(archive.read(name) for name in archive.namelist())
        assert b"SECRET_MARKER" not in content
        assert b"UNCOMMITTED_MARKER" not in content
        assert b"REPLAY_MARKER" not in content
    assert package.verify_archive(first) == manifest


def test_required_artifact_may_not_be_missing_or_symlink(snapshot_fixture):
    root, _ = snapshot_fixture
    path = root / "artifacts/submission.csv"
    path.unlink()
    with pytest.raises(ValueError, match="missing"):
        package.read_artifacts(root)
    path.symlink_to(root / ".env")
    with pytest.raises(ValueError, match="regular file"):
        package.read_artifacts(root)


def test_diagnostic_requirements_constrain_transitives_without_web_install(snapshot_fixture):
    _, sources = snapshot_fixture
    requirements = package.diagnostic_requirements(sources["requirements.lock"]).decode()
    assert "-c requirements.lock" in requirements
    assert "python-dotenv==" in requirements
    assert "pytest==" in requirements
    assert "Django" not in requirements and "-e ." not in requirements

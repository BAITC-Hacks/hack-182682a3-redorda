"""Create a deterministic diagnostic ZIP from a Git commit and named local reports."""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from zipfile import ZIP_STORED, ZipFile, ZipInfo

DEFAULT_COMMIT = "9711206"
SOURCE_FILES = (
    "agent.py",
    "pyproject.toml",
    "requirements.txt",
    "requirements.lock",
    "Makefile",
    "scripts/benchmark_agent.py",
    "scripts/build_agent.py",
    "scripts/run_official.py",
    "scripts/check_openai.py",
    "scripts/import_participant_kit.py",
    "docs/api-contract.md",
    "docs/developers/02-agent.md",
    "docs/developers/02-agent-integration.md",
)
OPTIONAL_SOURCE_FILES = (
    "scripts/diagnose_pilots.py",
    "scripts/diagnose_portfolio.py",
    "scripts/package_diagnostics.py",
)
REQUIRED_ARTIFACTS = ("benchmark.json", "submission.csv", "agent-validation.md")
OPTIONAL_ARTIFACTS = ("benchmark_evolve_smoke.json",)
DIAGNOSTIC_DEPENDENCIES = (
    "numpy",
    "openai",
    "pandas",
    "pydantic",
    "python-dotenv",
    "pytest",
    "ruff",
)
PYTEST_CONFIGURATION = """[pytest]
pythonpath = packages .
testpaths = packages/campaign_engine/tests
"""


def digest(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def git_bytes(root: Path, *arguments: str) -> bytes:
    result = subprocess.run(
        ["git", *arguments],
        cwd=root,
        check=True,
        capture_output=True,
    )
    return result.stdout


def selected_source_paths(paths: list[str]) -> list[str]:
    selected = set(SOURCE_FILES)
    missing = selected - set(paths)
    if missing:
        raise ValueError(f"Commit lacks required diagnostic sources: {sorted(missing)}")
    selected.update(set(OPTIONAL_SOURCE_FILES) & set(paths))
    for path in paths:
        if re.fullmatch(r"packages/campaign_engine/[^/]+\.py", path):
            selected.add(path)
        elif re.fullmatch(r"packages/campaign_engine/tests/test_[^/]+\.py", path):
            selected.add(path)
    if "packages/campaign_engine/engine_types.py" not in selected:
        raise ValueError("Commit lacks engine_types.py")
    return sorted(selected)


def snapshot_sources(root: Path, reference: str) -> tuple[str, int, dict[str, bytes]]:
    commit = (
        git_bytes(root, "rev-parse", "--verify", "--end-of-options", f"{reference}^{{commit}}")
        .decode()
        .strip()
    )
    timestamp = int(git_bytes(root, "show", "-s", "--format=%ct", commit))
    tracked = git_bytes(root, "ls-tree", "-rz", "--name-only", commit).decode().split("\0")
    source = {
        path: git_bytes(root, "show", f"{commit}:{path}") for path in selected_source_paths(tracked)
    }
    return commit, timestamp, source


def default_policy(source: bytes) -> str:
    tree = ast.parse(source.decode("utf-8"))
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == "EngineOptions":
            for field in node.body:
                if isinstance(field, ast.AnnAssign) and isinstance(field.target, ast.Name):
                    if field.target.id == "policy" and isinstance(field.value, ast.Constant):
                        return str(field.value.value)
    raise ValueError("Cannot determine the committed EngineOptions.policy default")


def diagnostic_requirements(lock: bytes) -> bytes:
    pinned = {}
    for line in lock.decode("utf-8").splitlines():
        match = re.fullmatch(r"([A-Za-z0-9_.-]+)==([^\s;]+)", line.strip())
        if match:
            pinned[match.group(1).lower()] = line.strip()
    missing = set(DIAGNOSTIC_DEPENDENCIES) - set(pinned)
    if missing:
        raise ValueError(f"Missing diagnostic dependencies in committed lock: {sorted(missing)}")
    lines = [
        "# AI runtime and tests, using the committed transitive constraints.",
        "-c requirements.lock",
        *(pinned[name] for name in DIAGNOSTIC_DEPENDENCIES),
    ]
    return ("\n".join(lines) + "\n").encode()


def read_artifacts(root: Path) -> tuple[dict[str, bytes], list[str]]:
    artifacts = {}
    absent = []
    for name in (*REQUIRED_ARTIFACTS, *OPTIONAL_ARTIFACTS):
        path = root / "artifacts" / name
        if path.is_symlink():
            raise ValueError(f"Diagnostic artifact must be a regular file: {name}")
        if not path.is_file():
            if name in REQUIRED_ARTIFACTS:
                raise ValueError(f"Required diagnostic artifact is missing: {name}")
            absent.append(name)
            continue
        artifacts[f"artifacts/{name}"] = path.read_bytes()
    return artifacts, absent


def report_provenance(content: bytes, sources: dict[str, bytes]) -> dict:
    report = json.loads(content)
    recorded = report.get("manifest", {})
    hashes = recorded.get("sources_sha256", {})
    comparisons = {}
    for path, expected in sorted(hashes.items()):
        actual = digest(sources[path]) if path in sources else None
        comparisons[path] = {
            "recorded_sha256": expected,
            "packaged_sha256": actual,
            "matches_packaged_bytes": actual == expected if actual is not None else None,
        }
    mismatches = [
        path for path, row in comparisons.items() if row["matches_packaged_bytes"] is not True
    ]
    return {
        "status": report.get("status"),
        "complete": report.get("complete"),
        "record_count": len(report.get("records", [])),
        "policies": sorted(report.get("summary", {})),
        "environment_seeds": recorded.get("environment_seeds", []),
        "recorded_engine_config": recorded.get("engine_config"),
        "source_comparison": comparisons,
        "source_hash_mismatches": mismatches,
        "exact_source_bytes_confirmed": bool(comparisons) and not mismatches,
        "post_run_provenance_preserved": "post_run_provenance" in report,
        "interpretation": (
            "Historical development results on the local mock. Original report bytes are "
            "preserved; source hashes are compared, not rewritten. A mismatch does not "
            "establish whether behavior changed, and does not certify this exact commit."
        ),
    }


def diagnostic_readme(commit: str, policy: str, reports: dict) -> bytes:
    mismatches = sum(len(report["source_hash_mismatches"]) for report in reports.values())
    text = f"""# AI-agent diagnostic snapshot

Исходники: `{commit}`. Default policy: `{policy}`.
Все исходные файлы прочитаны из Git этого коммита; локальные изменения в них не включены.
`manifest.json` содержит SHA-256 файлов и сопоставление с версиями из отчётов.

`artifacts/benchmark.json`, `submission.csv`, `agent-validation.md` и дополнительный
`benchmark_evolve_smoke.json`, если он есть, сохранены как исторические артефакты.
Это dev-результаты на локальном историческом mock, а не независимая контрольная выборка.
В отчётах найдено {mismatches} несовпадений исходных SHA-256 с файлами снимка;
это не подтверждает ни изменение алгоритма, ни выполнение точных байтов этого коммита.
Исходные даты, метрики, ошибки и provenance отчётов сохранены без изменения.

## Локальная проверка

Нужен Python 3.11+. В каталоге распакованного архива:

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements-diagnostics.txt
.venv/bin/python -m pytest -c pytest-diagnostics.ini -q
```

`requirements-diagnostics.txt` устанавливает AI runtime и тестовые инструменты;
транзитивные версии ограничены исходным `requirements.lock`. Файлы `pyproject.toml`,
`requirements.txt` и `Makefile` сохранены как контекст монорепозитория.

Для повторного локального benchmark нужен отдельно расположенный комплект данных
и инструментов оценки. Укажите абсолютный путь; исходные отчёты сохранятся:

```sh
export PARTICIPANT_KIT_DIR=/absolute/path/to/participant-kit
.venv/bin/python scripts/run_official.py local_eval --runs 10
.venv/bin/python scripts/benchmark_agent.py \\
  --runs 10 --policies adaptive fixed_100 fixed_200 wide_100 template \\
  --output artifacts/benchmark-reproduced.json
```

`Agent.act(env)` использует `{policy}` и не вызывает LLM автоматически.
Архив содержит только перечисленные в manifest исходники, отчёты и инструкции;
данные комплекта, настройки окружения и LLM replay в него не входят.
"""
    return text.encode("utf-8")


def build_archive(root: Path, output: Path, reference: str = DEFAULT_COMMIT) -> dict:
    commit, timestamp, sources = snapshot_sources(root, reference)
    policy = default_policy(sources["packages/campaign_engine/engine_types.py"])
    if reference == DEFAULT_COMMIT and policy != "adaptive":
        raise ValueError("The requested reference must preserve the adaptive default")
    artifacts, absent = read_artifacts(root)
    reports = {
        name: report_provenance(content, sources)
        for name, content in artifacts.items()
        if name.endswith(".json")
    }
    generated = {
        "README.md": diagnostic_readme(commit, policy, reports),
        "requirements-diagnostics.txt": diagnostic_requirements(sources["requirements.lock"]),
        "pytest-diagnostics.ini": PYTEST_CONFIGURATION.encode(),
    }
    payload = {**sources, **artifacts, **generated}
    manifest = {
        "format_version": 1,
        "source_commit": commit,
        "source_commit_time_utc": datetime.fromtimestamp(timestamp, timezone.utc).isoformat(),
        "default_policy": policy,
        "archive_kind": "local_diagnostic_snapshot",
        "reports_are_historical_development_mock_results": True,
        "report_provenance": reports,
        "missing_optional_artifacts": absent,
        "zip": {"compression": "stored", "entry_order": "sorted", "timestamp": "commit time"},
        "files": {
            name: {
                "sha256": digest(content),
                "size_bytes": len(content),
                "origin": "git_commit"
                if name in sources
                else ("local_artifact" if name in artifacts else "generated_diagnostic_support"),
            }
            for name, content in sorted(payload.items())
        },
    }
    payload["manifest.json"] = (
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False) + "\n"
    ).encode("utf-8")
    date = datetime.fromtimestamp(timestamp, timezone.utc)
    zip_date = (max(1980, date.year), date.month, date.day, date.hour, date.minute, date.second)
    output.parent.mkdir(parents=True, exist_ok=True)
    with ZipFile(output, "w", compression=ZIP_STORED) as archive:
        for name, content in sorted(payload.items()):
            info = ZipInfo(name, date_time=zip_date)
            info.create_system = 3
            info.external_attr = 0o100644 << 16
            archive.writestr(info, content)
    verify_archive(output)
    return manifest


def verify_archive(path: Path) -> dict:
    with ZipFile(path) as archive:
        manifest = json.loads(archive.read("manifest.json"))
        names = archive.namelist()
        expected = {*manifest["files"], "manifest.json"}
        if len(names) != len(expected) or set(names) != expected:
            raise ValueError("Archive contents differ from its manifest")
        for name, metadata in manifest["files"].items():
            content = archive.read(name)
            if digest(content) != metadata["sha256"] or len(content) != metadata["size_bytes"]:
                raise ValueError(f"Archive checksum mismatch: {name}")
    return manifest


def main():
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--commit", default=DEFAULT_COMMIT)
    parser.add_argument(
        "--output", type=Path, default=root / "artifacts/ai-agent-diagnostics-9711206.zip"
    )
    args = parser.parse_args()
    try:
        manifest = build_archive(root, args.output, args.commit)
    except (OSError, ValueError, subprocess.CalledProcessError) as exc:
        parser.exit(1, f"Diagnostic packaging failed: {exc}\n")
    print(f"Created {args.output} from {manifest['source_commit']}")
    print(f"SHA-256: {digest(args.output.read_bytes())}")
    print(f"Default policy: {manifest['default_policy']}; {len(manifest['files'])} payload files")


if __name__ == "__main__":
    main()

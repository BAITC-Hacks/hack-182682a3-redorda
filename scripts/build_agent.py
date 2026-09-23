"""Build a standalone agent from the shared engine's import graph."""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import shutil
import sys
from pathlib import Path

DEPENDENCIES = {
    "numpy": "numpy",
    "openai": "openai",
    "pandas": "pandas",
    "pydantic": "pydantic",
}


class BundleError(ValueError):
    """Invalid or ambiguous local-module merge."""


def internal_module(node: ast.ImportFrom, current: str) -> str | None:
    if node.level:
        parts = current.split(".")[: -node.level]
        module = ".".join([*parts, *([node.module] if node.module else [])])
    else:
        module = node.module or ""
    if module == "campaign_engine":
        raise BundleError("Import named symbols from a specific campaign_engine module")
    return module if module.startswith("campaign_engine.") else None


def import_graph(
    package: Path, entry: str = "campaign_engine.agent"
) -> list[tuple[str, ast.Module]]:
    """Resolve modules in dependency order."""
    ordered = []
    visited = set()
    active = set()

    def visit(module: str):
        if module in active:
            raise BundleError(f"Circular local imports at {module}")
        if module in visited:
            return
        active.add(module)
        path = package.joinpath(*module.split(".")[1:]).with_suffix(".py")
        if not path.is_file():
            raise BundleError(f"Cannot find local module {module}: {path}")
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                if any(alias.name.startswith("campaign_engine") for alias in node.names):
                    raise BundleError(f"Use from-imports for local symbols in {module}")
            elif isinstance(node, ast.ImportFrom):
                dependency = internal_module(node, module)
                if dependency:
                    if any(alias.name == "*" or alias.asname for alias in node.names):
                        raise BundleError(
                            f"Local wildcard/aliased imports are unsupported in {module}"
                        )
                    visit(dependency)
        active.remove(module)
        visited.add(module)
        ordered.append((module, tree))

    visit(entry)
    return ordered


class FlattenImports(ast.NodeTransformer):
    def __init__(self, module: str, futures: set[str], external: set[str]):
        self.module = module
        self.futures = futures
        self.external = external

    def visit_ImportFrom(self, node):
        if node.module == "__future__":
            self.futures.update(alias.name for alias in node.names)
            return ast.copy_location(ast.Pass(), node)
        if internal_module(node, self.module):
            return ast.copy_location(ast.Pass(), node)
        self.external.add((node.module or "").split(".")[0])
        return node

    def visit_Import(self, node):
        self.external.update(alias.name.split(".")[0] for alias in node.names)
        return node


def module_bindings(tree: ast.Module):
    """Collect module-scope names, including names inside conditional/try blocks."""

    def statements(nodes):
        for node in nodes:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                yield node.name, "definition"
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    yield alias.asname or alias.name.split(".")[0], f"import:{alias.name}"
            elif isinstance(node, ast.ImportFrom):
                for alias in node.names:
                    if alias.name == "*":
                        raise BundleError("Wildcard imports cannot be checked for name collisions")
                    yield alias.asname or alias.name, f"from:{node.module}:{alias.name}"
            else:
                targets = []
                if isinstance(node, ast.Assign):
                    targets = node.targets
                elif isinstance(node, (ast.AnnAssign, ast.AugAssign, ast.For, ast.AsyncFor)):
                    targets = [node.target]
                elif isinstance(node, (ast.With, ast.AsyncWith)):
                    targets = [item.optional_vars for item in node.items if item.optional_vars]
                for target in targets:
                    for name in ast.walk(target):
                        if isinstance(name, ast.Name) and isinstance(name.ctx, ast.Store):
                            yield name.id, "definition"
                for _field, value in ast.iter_fields(node):
                    if isinstance(value, list):
                        yield from statements(
                            [child for child in value if isinstance(child, ast.stmt)]
                        )
                        for child in value:
                            if isinstance(child, ast.ExceptHandler):
                                if child.name:
                                    yield child.name, "definition"
                                yield from statements(child.body)

    return statements(tree.body)


def render_bundle(package: Path) -> tuple[str, dict, set[str]]:
    sources = import_graph(package)
    futures = set()
    external = set()
    names = {}
    sections = []
    manifest = {"format_version": 1, "entrypoint": "campaign_engine.agent.Agent", "sources": {}}
    for module, tree in sources:
        source_path = package.joinpath(*module.split(".")[1:]).with_suffix(".py")
        manifest["sources"][module] = hashlib.sha256(source_path.read_bytes()).hexdigest()
        transformed = FlattenImports(module, futures, external).visit(tree)
        for name, binding in module_bindings(transformed):
            previous = names.get(name)
            if previous and previous[0] != module:
                identical_import = binding != "definition" and binding == previous[1]
                if not identical_import:
                    raise BundleError(
                        f"Global name collision: {name!r} in {previous[0]} and {module}"
                    )
            names[name] = (module, binding)
        if transformed.body and isinstance(transformed.body[0], ast.Expr):
            value = transformed.body[0].value
            if isinstance(value, ast.Constant) and isinstance(value.value, str):
                transformed.body.pop(0)
        sections.append(f"# Source: {module}\n{ast.unparse(transformed)}")
    header = (
        '"""Generated from the RedOrda shared engine.\n'
        'Build with: python scripts/build_agent.py\n"""\n'
    )
    if futures:
        header += "from __future__ import " + ", ".join(sorted(futures)) + "\n"
    source = header + "\n\n" + "\n\n\n".join(sections) + "\n"
    compile(source, "agent.py", "exec")  # Validate generated syntax.
    external -= sys.stdlib_module_names
    external.discard("__future__")
    return source, manifest, external


def minimal_requirements(root: Path, imports: set[str]) -> str:
    unknown = imports - set(DEPENDENCIES)
    if unknown:
        raise BundleError(f"Declare third-party dependencies before bundling: {sorted(unknown)}")
    pinned = {}
    for line in (root / "requirements.lock").read_text(encoding="utf-8").splitlines():
        if "==" in line and not line.lstrip().startswith("#"):
            pinned[line.split("==", 1)[0].lower()] = line
    requirements = []
    for dependency in sorted({DEPENDENCIES[name] for name in imports}):
        if dependency not in pinned:
            raise BundleError(f"Dependency {dependency!r} is absent from requirements.lock")
        requirements.append(pinned[dependency])
    return "# Runtime dependencies.\n" + (
        "\n".join(requirements) + "\n"
    )


DELIVERY_README = """# RedOrda — переносимый AI-агент

`agent.py` объединяет модули общего движка. `build-manifest.json` содержит их SHA-256.

Нужен Python 3.11+. Поместите `agent.py`, `local_eval.py` и `make_submission.py`
в каталог с CSV-данными. Выполните из этого каталога:

```sh
pip install -r requirements.txt
python local_eval.py --runs 10
python make_submission.py
```

`make_submission.py` создаёт `submission.csv`.

История загружается из `data/change_tariff.csv` относительно каталога агента
или из каталога, заданного абсолютным `PARTICIPANT_KIT_DIR`. Без этого файла
движок использует расчётную стратегию и пилоты.

`Agent.act(env)` работает в детерминированном режиме без вызовов LLM.
Наличие API-ключа автоматически не включает сетевые запросы.
"""


def build(root: Path, output: Path, submission: Path | None = None) -> dict:
    source, manifest, external = render_bundle(root / "packages/campaign_engine")
    requirements = minimal_requirements(root, external)
    manifest["agent_sha256"] = hashlib.sha256(source.encode()).hexdigest()
    manifest["direct_imports"] = sorted(external)
    output.mkdir(parents=True, exist_ok=True)
    (output / "agent.py").write_text(source, encoding="utf-8")
    (output / "requirements.txt").write_text(requirements, encoding="utf-8")
    (output / "README.md").write_text(DELIVERY_README, encoding="utf-8")
    (output / "build-manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    if submission is not None:
        shutil.copyfile(submission, output / "submission.csv")
    return manifest


def main():
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=root / "artifacts/delivery")
    parser.add_argument("--submission", type=Path, help="Copy an existing submission CSV")
    args = parser.parse_args()
    try:
        manifest = build(root, args.output, args.submission)
    except (BundleError, OSError, SyntaxError) as exc:
        parser.exit(1, f"Agent build failed: {exc}\n")
    print(f"Built {args.output / 'agent.py'} from {len(manifest['sources'])} shared modules")


if __name__ == "__main__":
    main()

import ast
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def imported_modules(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)
    return modules


def test_dashboard_dependency_direction():
    dashboard_root = PROJECT_ROOT / "src" / "hina_bot" / "dashboard"
    violations: list[str] = []

    for path in dashboard_root.rglob("*.py"):
        modules = imported_modules(path)
        for module in modules:
            if module.startswith("hina_bot.discord"):
                violations.append(f"{path}: dashboard -> discord import {module}")
            if module == "hina_bot.core.store":
                violations.append(f"{path}: dashboard -> runtime Store import {module}")

    assert violations == []


def test_core_does_not_depend_on_dashboard_transport():
    core_root = PROJECT_ROOT / "src" / "hina_bot" / "core"
    violations: list[str] = []

    for path in core_root.rglob("*.py"):
        modules = imported_modules(path)
        for module in modules:
            if module.startswith("hina_bot.dashboard"):
                violations.append(f"{path}: core -> dashboard import {module}")
            if module == "fastapi" or module.startswith("fastapi."):
                violations.append(f"{path}: core -> FastAPI import {module}")

    assert violations == []

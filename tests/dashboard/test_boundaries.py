import ast
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def imported_modules(path: Path) -> set[tuple[int, str]]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    modules: set[tuple[int, str]] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update((0, alias.name) for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.add((node.level, node.module))
    return modules


def test_dashboard_dependency_direction():
    dashboard_root = PROJECT_ROOT / "src" / "hina_bot" / "dashboard"
    violations: list[str] = []

    for path in dashboard_root.rglob("*.py"):
        for level, module in imported_modules(path):
            is_discord = module == "hina_bot.discord" or module.startswith("hina_bot.discord.")
            is_runtime_store = module == "hina_bot.core.store"
            if level:
                is_discord = is_discord or module == "discord" or module.startswith("discord.")
                is_runtime_store = is_runtime_store or module == "core.store"
            if is_discord:
                violations.append(f"{path}: dashboard -> discord import {module}")
            if is_runtime_store:
                violations.append(f"{path}: dashboard -> runtime Store import {module}")

    assert violations == []


def test_core_does_not_depend_on_dashboard_transport():
    core_root = PROJECT_ROOT / "src" / "hina_bot" / "core"
    violations: list[str] = []

    for path in core_root.rglob("*.py"):
        for level, module in imported_modules(path):
            is_dashboard = (
                module == "hina_bot.dashboard" or module.startswith("hina_bot.dashboard.")
            )
            if level:
                is_dashboard = (
                    is_dashboard
                    or module == "dashboard"
                    or module.startswith("dashboard.")
                )
            if is_dashboard:
                violations.append(f"{path}: core -> dashboard import {module}")
            if module == "fastapi" or module.startswith("fastapi."):
                violations.append(f"{path}: core -> FastAPI import {module}")

    assert violations == []

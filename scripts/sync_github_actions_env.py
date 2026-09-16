#!/usr/bin/env python3
"""Sync a dotenv file to GitHub Actions secrets and variables with gh."""

import argparse
import re
import shutil
import subprocess
from pathlib import Path

from dotenv import dotenv_values

_ENV_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_SECRET_SUFFIXES = ("_API_KEY", "_TOKEN", "_SECRET", "_PASSWORD", "_PRIVATE_KEY")


def default_env_file() -> Path:
    """Prefer the runtime file used by this project, then fall back to .env."""

    local = Path(".env.local")
    return local if local.is_file() else Path(".env")


def read_env(path: Path) -> dict[str, str]:
    """Read dotenv values without expanding references from the caller's environment."""

    if not path.is_file():
        raise ValueError(f"dotenv 파일을 찾을 수 없습니다: {path}")
    values = dotenv_values(path, interpolate=False)
    invalid = [name for name in values if not _ENV_NAME.fullmatch(name)]
    if invalid:
        raise ValueError(f"유효하지 않은 환경 변수 이름: {', '.join(invalid)}")
    return {name: value or "" for name, value in values.items()}


def is_secret_name(name: str, forced_secrets: set[str], forced_variables: set[str]) -> bool:
    """Classify credentials conservatively while allowing explicit overrides."""

    if name in forced_secrets:
        return True
    if name in forced_variables:
        return False
    return name.endswith(_SECRET_SUFFIXES)


def gh_set_command(kind: str, name: str, repo: str, environment: str) -> list[str]:
    command = ["gh", kind, "set", name]
    if repo:
        command.extend(("--repo", repo))
    if environment:
        command.extend(("--env", environment))
    return command


def sync(args: argparse.Namespace) -> tuple[int, int, int]:
    path = Path(args.env_file) if args.env_file else default_env_file()
    values = read_env(path)
    forced_secrets = set(args.secret)
    forced_variables = set(args.variable)
    overlap = forced_secrets & forced_variables
    if overlap:
        raise ValueError(f"secret과 variable에 동시에 지정됨: {', '.join(sorted(overlap))}")
    missing = (forced_secrets | forced_variables) - values.keys()
    if missing:
        raise ValueError(f"dotenv 파일에 없는 이름: {', '.join(sorted(missing))}")
    if not args.dry_run and shutil.which("gh") is None:
        raise ValueError("GitHub CLI(gh)를 찾을 수 없습니다. 설치 후 gh auth login을 실행하세요.")

    secret_count = 0
    variable_count = 0
    skipped_count = 0
    print(f"source: {path}")
    for name, value in values.items():
        kind = "secret" if is_secret_name(name, forced_secrets, forced_variables) else "variable"
        label = kind.upper()
        if not value and not args.include_empty:
            print(f"SKIP     {name} (empty {label})")
            skipped_count += 1
            continue
        if args.dry_run:
            print(f"DRY-RUN  {label} {name}")
        else:
            subprocess.run(
                gh_set_command(kind, name, args.repo, args.environment),
                input=value,
                text=True,
                check=True,
            )
            print(f"SET      {label} {name}")
        if kind == "secret":
            secret_count += 1
        else:
            variable_count += 1
    return secret_count, variable_count, skipped_count


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(
        description="dotenv 값을 GitHub Actions Secrets와 Variables로 분류해 등록합니다."
    )
    root.add_argument(
        "env_file",
        nargs="?",
        help="dotenv 경로. 생략하면 .env.local, .env 순서로 찾습니다.",
    )
    root.add_argument("--repo", help="OWNER/REPO. 생략하면 현재 gh 저장소를 사용합니다.")
    root.add_argument("--environment", help="production 같은 GitHub Environment 이름")
    root.add_argument("--dry-run", action="store_true", help="값을 등록하지 않고 분류 결과만 표시")
    root.add_argument(
        "--include-empty",
        action="store_true",
        help="빈 값도 등록합니다. 기본값은 기존 원격 값을 보호하기 위해 건너뜁니다.",
    )
    root.add_argument(
        "--secret",
        action="append",
        default=[],
        metavar="NAME",
        help="이름 규칙과 무관하게 Secret으로 분류. 반복 지정할 수 있습니다.",
    )
    root.add_argument(
        "--variable",
        action="append",
        default=[],
        metavar="NAME",
        help="이름 규칙과 무관하게 Variable로 분류. 반복 지정할 수 있습니다.",
    )
    return root


def main() -> None:
    root = parser()
    args = root.parse_args()
    try:
        secrets, variables, skipped = sync(args)
    except (OSError, ValueError, subprocess.CalledProcessError) as exc:
        root.exit(1, f"error: {exc}\n")
    print(f"done: {secrets} secrets, {variables} variables, {skipped} skipped")


if __name__ == "__main__":
    main()

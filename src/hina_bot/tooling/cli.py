"""Discover Hina CLI commands and display their help without running operations."""

from __future__ import annotations

import argparse
import subprocess
import sys
from collections.abc import Mapping
from dataclasses import dataclass
from importlib import metadata

DISTRIBUTION = "hina-discord-bot"
CATEGORY_ORDER = (
    "실행",
    "환경 설정",
    "DB · 유지보수",
    "Lore · 데이터",
    "평가 · 분석",
)


@dataclass(frozen=True)
class CommandInfo:
    category: str
    description: str
    native_help: bool = True
    warning: bool = False


# Descriptions/classifications only: the commands themselves come from installed entry points.
COMMAND_INFO: dict[str, CommandInfo] = {
    "hina-bot": CommandInfo("실행", "Discord 봇 실행", native_help=False),
    "hina-dashboard": CommandInfo("실행", "웹 관리 대시보드 실행", native_help=False),
    "hina-env-normalize": CommandInfo(
        "환경 설정", ".env 템플릿 정규화 및 백업", warning=True
    ),
    "hina-env-export": CommandInfo(
        "환경 설정", "DB runtime 설정을 dotenv로 추출", warning=True
    ),
    "hina-prepare-db": CommandInfo(
        "DB · 유지보수", "DB 스키마 확인 및 준비", warning=True
    ),
    "hina-flush-memory": CommandInfo(
        "DB · 유지보수", "대기 중인 기억 추출·저장", warning=True
    ),
    "hina-backfill-annotations": CommandInfo(
        "DB · 유지보수", "기존 기억 주석 데이터 보완", warning=True
    ),
    "hina-reset-conversation": CommandInfo(
        "DB · 유지보수", "대화 기록 초기화", warning=True
    ),
    "hina-reset-analysis-data": CommandInfo(
        "DB · 유지보수", "분석 데이터 및 로그 초기화", warning=True
    ),
    "hina-lore": CommandInfo("Lore · 데이터", "Lore 데이터 검증·관리"),
    "hina-eval": CommandInfo("평가 · 분석", "대화 평가 실행"),
    "hina-routing-eval": CommandInfo("평가 · 분석", "모델 라우팅 평가"),
    "hina-memory-report": CommandInfo("평가 · 분석", "장기 기억 통계 보고서"),
    "hina-memory-extraction-eval": CommandInfo("평가 · 분석", "기억 추출 평가"),
    "hina-memory-response-eval": CommandInfo("평가 · 분석", "기억 활용 응답 평가"),
}


def installed_commands() -> dict[str, metadata.EntryPoint]:
    """Read this distribution's console scripts, not other installed applications."""
    distribution = metadata.distribution(DISTRIBUTION)
    return {
        point.name: point
        for point in distribution.entry_points
        if point.group == "console_scripts" and point.name.startswith("hina-")
    }


def command_name(value: str, commands: Mapping[str, metadata.EntryPoint]) -> str | None:
    candidate = value if value.startswith("hina-") else f"hina-{value}"
    return candidate if candidate in commands else None


def print_commands(commands: Mapping[str, metadata.EntryPoint]) -> None:
    print(f"Hina Discord Bot CLI — {len(commands)}개 명령어\n")
    for category in (*CATEGORY_ORDER, "기타"):
        group = [
            name for name in commands
            if COMMAND_INFO.get(name, CommandInfo("기타", "설명 미등록")).category == category
        ]
        if not group:
            continue
        print(f"[{category}]")
        for name in sorted(group):
            info = COMMAND_INFO.get(name, CommandInfo("기타", "설명 미등록"))
            caution = " [주의]" if info.warning else ""
            print(f"  {name:<30} {info.description}{caution}")
        print()
    print("상세 도움말: hina help <명령어>")
    print("예: hina help env-export")
    print("[주의] 명령어는 실제 실행 시 파일 또는 DB를 변경할 수 있습니다.")


def show_command_help(
    value: str, commands: Mapping[str, metadata.EntryPoint]
) -> int:
    name = command_name(value, commands)
    if name is None:
        print(f"알 수 없는 명령어: {value}. 'hina commands'로 목록을 확인하세요.", file=sys.stderr)
        return 2

    info = COMMAND_INFO.get(name)
    if info is not None:
        caution = "\n주의: 실제 실행 시 파일 또는 DB가 변경될 수 있습니다." if info.warning else ""
        print(f"{name} — {info.description}{caution}", flush=True)
        if not info.native_help:
            print("이 명령어는 자체 --help를 지원하지 않는 실행 명령어입니다.")
            print(f"실행: {name}")
            return 0
    else:
        print(f"{name} — 설명 미등록", flush=True)

    # Invoke only the documented --help path, in a separate process. Never load and
    # call a command's main() directly: bot/dashboard would actually start services.
    point = commands[name]
    try:
        return subprocess.run(
            [sys.executable, "-m", point.module, "--help"],
            check=False,
        ).returncode
    except OSError as exc:
        print(f"도움말 실행 실패: {exc}", file=sys.stderr)
        return 2


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="hina",
        description="Hina Discord Bot 명령어 검색 및 도움말",
    )
    subparsers = parser.add_subparsers(dest="action")
    subparsers.add_parser("commands", help="설치된 Hina 명령어를 그룹별로 표시")
    help_parser = subparsers.add_parser("help", help="특정 명령어의 도움말 표시")
    help_parser.add_argument("command", help="예: env-export 또는 hina-env-export")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)

    if args.action is None:
        parser.print_help()
        print("\n사용 가능한 명령어: hina commands")
        return 0

    try:
        commands = installed_commands()
    except metadata.PackageNotFoundError:
        print(
            "Hina 패키지 설치 정보를 찾을 수 없습니다. 'uv sync' 또는 "
            "'pip install -e .'로 설치하세요.",
            file=sys.stderr,
        )
        return 2

    if args.action == "commands":
        print_commands(commands)
        return 0
    if args.action == "help":
        return show_command_help(args.command, commands)
    parser.error(f"Unknown action: {args.action}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())

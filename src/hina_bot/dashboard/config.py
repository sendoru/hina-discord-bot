from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from dotenv import load_dotenv


@dataclass(frozen=True)
class DashboardSettings:
    database_path: str = "data/hina.sqlite3"
    usage_log_path: str = "data/logs/usage.jsonl"
    event_log_path: str = "data/logs/events.jsonl"
    host: str = "127.0.0.1"
    port: int = 8765
    timezone: str = "Asia/Seoul"

    @classmethod
    def load(cls) -> DashboardSettings:
        load_dotenv(Path.cwd() / ".env.local", override=False)
        load_dotenv(Path.cwd() / ".env", override=False)

        database_path = (
            os.getenv("DASHBOARD_DATABASE_PATH", "").strip()
            or os.getenv("DATABASE_PATH", "data/hina.sqlite3").strip()
        )
        usage_log_path = (
            os.getenv("DASHBOARD_USAGE_LOG_PATH", "").strip()
            or os.getenv("USAGE_LOG_PATH", "data/logs/usage.jsonl").strip()
        )
        event_log_path = (
            os.getenv("DASHBOARD_EVENT_LOG_PATH", "").strip()
            or os.getenv("EVENT_LOG_PATH", "data/logs/events.jsonl").strip()
        )
        host = os.getenv("DASHBOARD_HOST", "127.0.0.1").strip()
        if not host or any(char in host for char in "\r\n\0"):
            raise ValueError("DASHBOARD_HOST는 줄바꿈 없는 호스트여야 합니다.")
        try:
            port = int(os.getenv("DASHBOARD_PORT", "8765"))
        except ValueError as exc:
            raise ValueError("DASHBOARD_PORT는 정수여야 합니다.") from exc
        if not 1 <= port <= 65535:
            raise ValueError("DASHBOARD_PORT는 1~65535 범위여야 합니다.")
        if not database_path:
            raise ValueError("DASHBOARD_DATABASE_PATH 또는 DATABASE_PATH를 설정해 주세요.")

        timezone = (
            os.getenv("DASHBOARD_TIMEZONE", "").strip()
            or os.getenv("RUNTIME_TIMEZONE", "").strip()
            or "Asia/Seoul"
        )
        try:
            ZoneInfo(timezone)
        except ZoneInfoNotFoundError as exc:
            raise ValueError(f"알 수 없는 DASHBOARD_TIMEZONE입니다: {timezone}") from exc

        return cls(
            database_path=database_path,
            usage_log_path=usage_log_path,
            event_log_path=event_log_path,
            host=host,
            port=port,
            timezone=timezone,
        )

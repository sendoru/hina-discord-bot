"""Trusted runtime context that changes with the real-world clock."""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

_WEEKDAYS_KO = ("월요일", "화요일", "수요일", "목요일", "금요일", "토요일", "일요일")


def build_runtime_context(settings, *, now: datetime | None = None) -> dict[str, str]:
    """Build small trusted context for resolving relative dates and local time."""
    timezone = getattr(settings, "runtime_timezone", "Asia/Seoul") or "Asia/Seoul"
    locale = getattr(settings, "runtime_locale", "ko-KR") or "ko-KR"
    default_location = getattr(settings, "runtime_default_location", "") or ""
    zone = ZoneInfo(timezone)
    current = now.astimezone(zone) if now is not None else datetime.now(zone)
    context = {
        "current_datetime": current.isoformat(timespec="seconds"),
        "current_date": current.date().isoformat(),
        "current_time": current.strftime("%H:%M:%S"),
        "weekday": _WEEKDAYS_KO[current.weekday()],
        "timezone": timezone,
        "locale": locale,
    }
    if default_location:
        context["default_location"] = default_location[:100]
    return context


def runtime_instruction(context: dict[str, str]) -> str:
    """Render trusted runtime facts as a compact system instruction."""
    location = context.get("default_location")
    label = f"기본 지역: {location}" if location else "기본 지역: 설정되지 않음"
    lines = (
        "[현재 시점]\n"
        f"{context['current_datetime']} ({context['weekday']}), timezone={context['timezone']}, "
        f"locale={context['locale']}, {label}.\n"
        "상대적 시간은 이 시각을 기준으로 해석하고 현재 날짜·시각 자체는 검색하지 마세요."
    )
    if location:
        return lines + " 기본 지역은 지역 생략 시 fallback일 뿐 사용자의 실제 현재 위치라고 주장하지 마세요."
    return lines + " 지역 의존 질문에 지역이 없으면 추측하지 말고 필요한 지역을 물어보세요."

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
    if location:
        location_lines = (
            f"기본 지역: {location}\n"
            "사용자가 지역을 생략한 지역 의존 질문에는 이 지역을 기본값으로 사용할 수 있습니다. "
            "이 값은 사용자의 실제 현재 위치라고 주장하지 마세요.\n"
        )
    else:
        location_lines = (
            "기본 지역: 설정되지 않음\n"
            "날씨·교통·영업시간처럼 지역이 필요한데 사용자가 지역을 주지 않았다면 위치를 추측하지 말고 필요한 지역을 물어보세요.\n"
        )
    return (
        "[현재 시점]\n"
        f"기준 시각: {context['current_datetime']} ({context['weekday']})\n"
        f"시간대: {context['timezone']} / locale: {context['locale']}\n"
        + location_lines
        + "오늘·내일·이번 주·몇 시간 뒤 같은 상대적 시간 표현은 위 기준 시각으로 해석하세요. "
        "현재 날짜나 시각 자체를 답할 때는 외부 검색보다 이 런타임 값을 우선하세요."
    )

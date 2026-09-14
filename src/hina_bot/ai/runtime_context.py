"""Trusted runtime context that changes with the real-world clock."""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

_WEEKDAYS_KO = ("월요일", "화요일", "수요일", "목요일", "금요일", "토요일", "일요일")


def _daypart(hour: int) -> str:
    if hour < 5:
        return "새벽"
    if hour < 9:
        return "아침"
    if hour < 12:
        return "오전"
    if hour < 18:
        return "오후"
    if hour < 22:
        return "저녁"
    return "밤"


def _season(month: int) -> str:
    if month in {3, 4, 5}:
        return "봄"
    if month in {6, 7, 8}:
        return "여름"
    if month in {9, 10, 11}:
        return "가을"
    return "겨울"


def build_runtime_context(settings, *, now: datetime | None = None) -> dict[str, str]:
    """Build small trusted context for resolving relative dates and ambient time cues."""
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
        "daypart": _daypart(current.hour),
        "season": _season(current.month),
        "day_type": "주말" if current.weekday() >= 5 else "평일",
        "timezone": timezone,
        "locale": locale,
    }
    if default_location:
        context["default_location"] = default_location[:100]
    return context


def runtime_instruction(context: dict[str, str]) -> str:
    """Render trusted runtime facts and lightweight ambient-awareness guidance."""
    location = context.get("default_location")
    label = f"기본 지역: {location}" if location else "기본 지역: 설정되지 않음"
    lines = (
        "[현재 시점]\n"
        f"{context['current_datetime']} ({context['weekday']}, {context['day_type']}), "
        f"시간대={context['daypart']}, 계절={context['season']}, timezone={context['timezone']}, "
        f"locale={context['locale']}, {label}.\n"
        "상대적 시간은 이 시각을 기준으로 해석하고 현재 날짜·시각 자체는 검색하지 마세요. "
        "현재 시각·요일·시간대·계절은 사용자의 상태·일정·행동과 관련 있을 때 대화에 자연스럽게 "
        "반영하되, 관련 없으면 굳이 언급하지 마세요. 제공된 현재 시점과 모순되는 시간대 표현을 "
        "만들지 마세요. 날씨처럼 제공되지 않은 현재 환경 정보는 추측하지 마세요."
    )
    if location:
        return lines + " 기본 지역은 지역 생략 시 fallback일 뿐 사용자의 실제 현재 위치라고 주장하지 마세요."
    return lines + " 지역 의존 질문에 지역이 없으면 추측하지 말고 필요한 지역을 물어보세요."

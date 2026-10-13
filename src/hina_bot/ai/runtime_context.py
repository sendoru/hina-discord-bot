"""Trusted runtime context that changes with the real-world clock."""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from .ambient_weather import CURRENT_AMBIENT_WEATHER

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


def build_runtime_context(settings, *, now: datetime | None = None) -> dict[str, object]:
    """Build small trusted context for resolving relative dates and ambient real-world cues."""
    timezone = getattr(settings, "runtime_timezone", "Asia/Seoul") or "Asia/Seoul"
    locale = getattr(settings, "runtime_locale", "ko-KR") or "ko-KR"
    default_location = getattr(settings, "runtime_default_location", "") or ""
    zone = ZoneInfo(timezone)
    current = now.astimezone(zone) if now is not None else datetime.now(zone)
    context: dict[str, object] = {
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
    weather = CURRENT_AMBIENT_WEATHER.get()
    if weather is not None:
        context["weather"] = weather.as_context()
    return context


def _weather_line(weather: dict[str, object]) -> str:
    details = [f"날씨={weather.get('condition', '현재 날씨')}"]
    optional = (
        ("temperature_c", "기온", "°C"),
        ("apparent_temperature_c", "체감", "°C"),
        ("humidity_percent", "습도", "%"),
        ("precipitation_mm", "최근 강수", "mm"),
        ("wind_speed_kmh", "바람", "km/h"),
    )
    for key, label, unit in optional:
        value = weather.get(key)
        if value is not None:
            details.append(f"{label}={value}{unit}")
    daylight = weather.get("is_day")
    if isinstance(daylight, bool):
        details.append("주야=낮" if daylight else "주야=밤")
    return ", ".join(details)


def runtime_instruction(context: dict[str, object]) -> str:
    """Render trusted runtime facts and lightweight ambient-awareness guidance."""
    location = context.get("default_location")
    label = f"기본 지역: {location}" if location else "기본 지역: 설정되지 않음"
    lines = (
        "[현재 시점]\n"
        f"{context['current_datetime']} ({context['weekday']}, {context['day_type']}), "
        f"시간대={context['daypart']}, 계절={context['season']}, timezone={context['timezone']}, "
        f"locale={context['locale']}, {label}.\n"
        "이 시점은 신뢰된 런타임 사실이며, 대화·기억에서 주장하는 현재 시각과 충돌하면 "
        "설정된 timezone 기준으로 구분하세요. 다른 지역·시간대가 명시되면 그 기준을 따로 "
        "고려하고, 가정된 시각을 실제 현재 시점과 혼동하지 마세요. 상대적 시간은 현재 시점을 "
        "기준으로 해석하고 현재 날짜·시각 자체는 검색하지 마세요. 시간 정보는 관련 있을 때만 "
        "자연스럽게 반영하세요."
    )
    weather = context.get("weather")
    if isinstance(weather, dict):
        weather_location = weather.get("location", location or "기본 지역")
        weather_at = weather.get("at", "")
        weather_timezone = weather.get("timezone", context["timezone"])
        lines += (
            "\n[현재 환경]\n"
            f"{weather_location} 기준 {weather_at} ({weather_timezone}), {_weather_line(weather)}.\n"
            "환경 정보는 관련된 대화에서만 활용하세요. 기본 지역의 날씨를 사용자의 실제 위치나 "
            "세계관 내부 날씨로 단정하지 말고, 최신 상태를 직접 물어 외부 확인이 가능하면 "
            "확인 결과를 우선하세요."
        )
    else:
        lines += " 날씨처럼 제공되지 않은 현재 환경 정보는 추측하지 마세요."
    if location:
        return lines + " 기본 지역은 사용자의 실제 위치가 아닌 fallback입니다."
    return lines

"""Small cached weather snapshot for ambient real-world conversation cues."""

from __future__ import annotations

import asyncio
import json
import logging
import time
from contextvars import ContextVar
from dataclasses import dataclass
from functools import lru_cache
from urllib.parse import urlencode
from urllib.request import Request, urlopen

log = logging.getLogger("hina")

WEATHER_TTL_SECONDS = 20 * 60
WEATHER_RETRY_SECONDS = 60
_REQUEST_TIMEOUT_SECONDS = 4
_MAX_RESPONSE_BYTES = 1_000_000

_WEATHER_LABELS = {
    0: "맑음",
    1: "대체로 맑음",
    2: "구름 조금",
    3: "흐림",
    45: "안개",
    48: "서리 안개",
    51: "약한 이슬비",
    53: "이슬비",
    55: "강한 이슬비",
    56: "약한 어는 이슬비",
    57: "강한 어는 이슬비",
    61: "약한 비",
    63: "비",
    65: "강한 비",
    66: "약한 어는 비",
    67: "강한 어는 비",
    71: "약한 눈",
    73: "눈",
    75: "강한 눈",
    77: "싸락눈",
    80: "약한 소나기",
    81: "소나기",
    82: "강한 소나기",
    85: "약한 눈 소나기",
    86: "강한 눈 소나기",
    95: "뇌우",
    96: "약한 우박을 동반한 뇌우",
    99: "강한 우박을 동반한 뇌우",
}


@dataclass(frozen=True)
class WeatherSnapshot:
    location: str
    at: str
    timezone: str
    condition: str
    temperature_c: float | None = None
    apparent_temperature_c: float | None = None
    humidity_percent: int | None = None
    precipitation_mm: float | None = None
    wind_speed_kmh: float | None = None
    is_day: bool | None = None

    def as_context(self) -> dict[str, str | int | float | bool]:
        result: dict[str, str | int | float | bool] = {
            "location": self.location,
            "at": self.at,
            "timezone": self.timezone,
            "condition": self.condition,
        }
        optional = {
            "temperature_c": self.temperature_c,
            "apparent_temperature_c": self.apparent_temperature_c,
            "humidity_percent": self.humidity_percent,
            "precipitation_mm": self.precipitation_mm,
            "wind_speed_kmh": self.wind_speed_kmh,
            "is_day": self.is_day,
        }
        result.update({key: value for key, value in optional.items() if value is not None})
        return result


CURRENT_AMBIENT_WEATHER: ContextVar[WeatherSnapshot | None] = ContextVar(
    "current_ambient_weather", default=None
)


def _json_get(url: str) -> dict:
    request = Request(url, headers={"User-Agent": "hina-discord-bot/0.1"})
    with urlopen(request, timeout=_REQUEST_TIMEOUT_SECONDS) as response:
        body = response.read(_MAX_RESPONSE_BYTES + 1)
    if len(body) > _MAX_RESPONSE_BYTES:
        raise ValueError("Weather response too large")
    value = json.loads(body)
    if not isinstance(value, dict):
        raise TypeError("Weather response is not an object")
    return value


def _float(value) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return round(float(value), 1)
    except (TypeError, ValueError):
        return None


def _int(value) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


@lru_cache(maxsize=16)
def _geocode(location: str, language: str) -> tuple[float, float]:
    geocode_url = "https://geocoding-api.open-meteo.com/v1/search?" + urlencode({
        "name": location,
        "count": 1,
        "language": language,
        "format": "json",
    })
    geocode = _json_get(geocode_url)
    results = geocode.get("results")
    if not isinstance(results, list) or not results:
        raise ValueError("Runtime default location could not be geocoded")
    place = results[0]
    if not isinstance(place, dict):
        raise TypeError("Invalid geocoding result")
    latitude = _float(place.get("latitude"))
    longitude = _float(place.get("longitude"))
    if latitude is None or longitude is None:
        raise ValueError("Geocoding result has no coordinates")
    return latitude, longitude


def fetch_weather_snapshot(location: str, locale: str, timezone: str) -> WeatherSnapshot:
    language = locale.split("-", 1)[0].lower()
    if len(language) != 2 or not language.isalpha():
        language = "en"
    latitude, longitude = _geocode(location, language)

    forecast_url = "https://api.open-meteo.com/v1/forecast?" + urlencode({
        "latitude": latitude,
        "longitude": longitude,
        "current": (
            "temperature_2m,apparent_temperature,relative_humidity_2m,precipitation,"
            "weather_code,wind_speed_10m,is_day"
        ),
        "timezone": timezone,
        "forecast_days": 1,
    })
    forecast = _json_get(forecast_url)
    current = forecast.get("current")
    if not isinstance(current, dict):
        raise TypeError("Weather response has no current conditions")

    code = _int(current.get("weather_code"))
    condition = _WEATHER_LABELS.get(code, "현재 날씨")
    is_day_value = _int(current.get("is_day"))
    return WeatherSnapshot(
        location=location,
        at=str(current.get("time", ""))[:32],
        timezone=timezone,
        condition=condition,
        temperature_c=_float(current.get("temperature_2m")),
        apparent_temperature_c=_float(current.get("apparent_temperature")),
        humidity_percent=_int(current.get("relative_humidity_2m")),
        precipitation_mm=_float(current.get("precipitation")),
        wind_speed_kmh=_float(current.get("wind_speed_10m")),
        is_day=None if is_day_value is None else bool(is_day_value),
    )


class AmbientWeatherCache:
    """Refresh current weather at most once per TTL for the configured fallback location."""

    def __init__(self, *, fetcher=fetch_weather_snapshot, clock=time.monotonic):
        self._fetcher = fetcher
        self._clock = clock
        self._key: tuple[str, str, str] | None = None
        self._snapshot: WeatherSnapshot | None = None
        self._fetched_at = 0.0
        self._retry_after = 0.0
        self._lock = asyncio.Lock()

    async def current(self, settings) -> WeatherSnapshot | None:
        location = (getattr(settings, "runtime_default_location", "") or "").strip()
        if not location:
            return None
        locale = getattr(settings, "runtime_locale", "ko-KR") or "ko-KR"
        timezone = getattr(settings, "runtime_timezone", "Asia/Seoul") or "Asia/Seoul"
        key = (location, locale, timezone)
        now = self._clock()
        if (
            key == self._key
            and self._snapshot is not None
            and now - self._fetched_at < WEATHER_TTL_SECONDS
        ):
            return self._snapshot
        if key == self._key and now < self._retry_after:
            return None

        async with self._lock:
            now = self._clock()
            if (
                key == self._key
                and self._snapshot is not None
                and now - self._fetched_at < WEATHER_TTL_SECONDS
            ):
                return self._snapshot
            if key == self._key and now < self._retry_after:
                return None
            try:
                snapshot = await asyncio.to_thread(self._fetcher, location, locale, timezone)
            except (OSError, TypeError, ValueError) as exc:
                self._key = key
                self._snapshot = None
                self._retry_after = now + WEATHER_RETRY_SECONDS
                log.warning("Ambient weather refresh failed (%s)", type(exc).__name__)
                return None
            self._key = key
            self._snapshot = snapshot
            self._fetched_at = now
            self._retry_after = 0.0
            return snapshot

from datetime import UTC, datetime
from types import SimpleNamespace as NS

import pytest

from hina_bot.ai.ambient_weather import (
    CURRENT_AMBIENT_WEATHER,
    WEATHER_RETRY_SECONDS,
    WEATHER_TTL_SECONDS,
    AmbientWeatherCache,
    WeatherSnapshot,
)
from hina_bot.ai.runtime_context import build_runtime_context, runtime_instruction


def _settings(location="서울"):
    return NS(
        runtime_default_location=location,
        runtime_locale="ko-KR",
        runtime_timezone="Asia/Seoul",
    )


def _snapshot(condition="비"):
    return WeatherSnapshot(
        location="서울",
        at="2026-09-14T12:40",
        timezone="Asia/Seoul",
        condition=condition,
        temperature_c=21.2,
        apparent_temperature_c=20.5,
        humidity_percent=72,
        precipitation_mm=0.6,
        wind_speed_kmh=8.4,
        is_day=True,
    )


@pytest.mark.asyncio
async def test_weather_cache_reuses_snapshot_until_ttl():
    now = [100.0]
    calls = []

    def fetcher(location, locale, timezone):
        calls.append((location, locale, timezone))
        return _snapshot()

    cache = AmbientWeatherCache(fetcher=fetcher, clock=lambda: now[0])
    first = await cache.current(_settings())
    second = await cache.current(_settings())
    assert first == second
    assert len(calls) == 1

    now[0] += WEATHER_TTL_SECONDS + 1
    await cache.current(_settings())
    assert len(calls) == 2


@pytest.mark.asyncio
async def test_weather_cache_skips_when_default_location_is_empty():
    calls = []

    def fetcher(*args):
        calls.append(args)
        return _snapshot()

    cache = AmbientWeatherCache(fetcher=fetcher)
    assert await cache.current(_settings("")) is None
    assert calls == []


@pytest.mark.asyncio
async def test_weather_failure_backs_off_without_reusing_stale_snapshot():
    now = [100.0]
    calls = []

    def fetcher(*args):
        calls.append(args)
        raise OSError("offline")

    cache = AmbientWeatherCache(fetcher=fetcher, clock=lambda: now[0])
    assert await cache.current(_settings()) is None
    assert await cache.current(_settings()) is None
    assert len(calls) == 1

    now[0] += WEATHER_RETRY_SECONDS + 1
    assert await cache.current(_settings()) is None
    assert len(calls) == 2


def test_runtime_instruction_includes_request_scoped_weather_snapshot():
    token = CURRENT_AMBIENT_WEATHER.set(_snapshot())
    try:
        context = build_runtime_context(
            _settings(),
            now=datetime(2026, 9, 14, 3, 48, tzinfo=UTC),
        )
    finally:
        CURRENT_AMBIENT_WEATHER.reset(token)

    instruction = runtime_instruction(context)
    assert "[현재 환경]" in instruction
    assert "서울 기준 2026-09-14T12:40" in instruction
    assert "날씨=비" in instruction
    assert "기온=21.2°C" in instruction
    assert "체감=20.5°C" in instruction
    assert "매 답변마다 억지로 언급하지 마세요" in instruction
    assert "세계관 내부 장소·사건" in instruction

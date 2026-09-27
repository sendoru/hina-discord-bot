import asyncio
from datetime import UTC, datetime
from types import SimpleNamespace as NS

import pytest

from hina_bot.ai.ambient_weather import (
    CURRENT_AMBIENT_WEATHER,
    WEATHER_MAX_STALE_SECONDS,
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
async def test_weather_current_returns_immediately_and_refreshes_missing_cache_in_background():
    calls = []

    def fetcher(location, locale, timezone):
        calls.append((location, locale, timezone))
        return _snapshot()

    cache = AmbientWeatherCache(fetcher=fetcher)

    assert cache.current(_settings()) is None
    assert calls == []

    task = cache._refresh_task
    assert task is not None
    await task

    assert cache.current(_settings()) == _snapshot()
    assert len(calls) == 1
    await cache.close()


@pytest.mark.asyncio
async def test_weather_cache_returns_stale_snapshot_while_background_refresh_runs():
    now = [100.0]
    calls = []
    snapshots = [_snapshot("비"), _snapshot("맑음")]

    def fetcher(*args):
        calls.append(args)
        return snapshots[min(len(calls) - 1, len(snapshots) - 1)]

    cache = AmbientWeatherCache(fetcher=fetcher, clock=lambda: now[0])
    assert await cache.refresh(_settings())
    assert cache.current(_settings()).condition == "비"

    now[0] += WEATHER_TTL_SECONDS + 1
    stale = cache.current(_settings())
    assert stale is not None
    assert stale.condition == "비"

    task = cache._refresh_task
    assert task is not None
    await task

    assert cache.current(_settings()).condition == "맑음"
    assert len(calls) == 2
    await cache.close()


@pytest.mark.asyncio
async def test_weather_cache_drops_snapshot_after_max_stale_age():
    now = [100.0]
    calls = []

    def fetcher(*args):
        calls.append(args)
        return _snapshot()

    cache = AmbientWeatherCache(fetcher=fetcher, clock=lambda: now[0])
    assert await cache.refresh(_settings())

    now[0] += WEATHER_MAX_STALE_SECONDS + 1
    assert cache.current(_settings()) is None

    task = cache._refresh_task
    assert task is not None
    await task
    assert len(calls) == 2
    await cache.close()


@pytest.mark.asyncio
async def test_weather_cache_skips_when_default_location_is_empty():
    calls = []

    def fetcher(*args):
        calls.append(args)
        return _snapshot()

    cache = AmbientWeatherCache(fetcher=fetcher)
    assert cache.current(_settings("")) is None
    assert calls == []
    assert cache._refresh_task is None
    await cache.close()


@pytest.mark.asyncio
async def test_weather_failure_preserves_stale_snapshot_and_backs_off():
    now = [100.0]
    calls = []
    fail = [False]

    def fetcher(*args):
        calls.append(args)
        if fail[0]:
            raise OSError("offline")
        return _snapshot()

    cache = AmbientWeatherCache(fetcher=fetcher, clock=lambda: now[0])
    assert await cache.refresh(_settings())
    fail[0] = True

    now[0] += WEATHER_TTL_SECONDS + 1
    stale = cache.current(_settings())
    assert stale == _snapshot()

    task = cache._refresh_task
    assert task is not None
    await task
    assert len(calls) == 2

    # Failed refresh keeps the stale snapshot and suppresses immediate retry.
    assert cache.current(_settings()) == _snapshot()
    assert cache._refresh_task is None
    assert len(calls) == 2

    now[0] += WEATHER_RETRY_SECONDS + 1
    assert cache.current(_settings()) == _snapshot()
    retry = cache._refresh_task
    assert retry is not None
    await retry
    assert len(calls) == 3
    await cache.close()


@pytest.mark.asyncio
async def test_weather_poll_starts_immediately_and_close_cancels_it():
    started = asyncio.Event()

    async def fake_refresh(settings, *, force=False):
        assert force is True
        started.set()
        return True

    cache = AmbientWeatherCache()
    cache.refresh = fake_refresh
    cache.start_polling(_settings())

    await asyncio.wait_for(started.wait(), timeout=1.0)
    poll_task = cache._poll_task
    assert poll_task is not None
    assert not poll_task.done()

    await cache.close()
    assert poll_task.done()


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

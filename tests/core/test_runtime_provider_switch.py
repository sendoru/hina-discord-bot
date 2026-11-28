import asyncio
from contextlib import closing
from unittest.mock import AsyncMock, Mock

import pytest

from hina_bot.ai.llm import LLM
from hina_bot.core.config import Settings
from hina_bot.core.runtime_config import RuntimeSettings, pin_provider_route
from hina_bot.core.store import Store


def route(provider="gemini"):
    return {
        "provider": provider,
        "model": "gemini-fixed",
        "fast_model": "gemini-fast",
        "smart_model": "gemini-smart",
        "output_repair_model": "auto",
    }


def test_atomic_route_persistence_and_reset():
    with closing(Store.create(":memory:")) as store:
        base = Settings(discord_token="test", openai_api_key="test", gemini_api_key="test")
        settings = RuntimeSettings(base, store)
        with pytest.raises(ValueError, match="API key"):
            settings.set_provider_route(route("openrouter"))
        assert store.settings.runtime_values() == {}
        with pytest.raises(ValueError):
            settings.set_provider_route({"provider": "gemini"})
        assert store.settings.runtime_values() == {}
        settings.set_provider_route(route())
        assert settings.provider == "gemini"
        assert settings.fast_model == "gemini-fast"
        restored = RuntimeSettings(base, store)
        assert restored.provider == "gemini"
        assert restored.smart_model == "gemini-smart"
        with pytest.raises(ValueError, match="complete provider route"):
            restored.reset("model")
        with pytest.raises(ValueError, match="atomic"):
            restored.set_text("provider", "openai")
        restored.reset("provider")
        assert restored.provider == base.provider
        assert restored.model == base.model
        assert store.settings.runtime_values() == {}


def test_concurrent_route_pinning_and_client_lifetime(monkeypatch, tmp_path):
    async def run():
        with closing(Store.create(":memory:")) as store:
            base = Settings(
                discord_token="test", openai_api_key="test", gemini_api_key="test",
                usage_log_path=str(tmp_path / "usage.jsonl"),
            )
            settings = RuntimeSettings(base, store)
            openai = Mock(provider_name="openai", close=AsyncMock())
            gemini = Mock(provider_name="gemini", close=AsyncMock())
            llm = LLM(settings, openai)
            monkeypatch.setattr(llm, "_create_primary_client", lambda: gemini)
            entered = asyncio.Event()
            resume = asyncio.Event()

            @pin_provider_route
            async def old_turn(owner):
                entered.set()
                await resume.wait()
                assert owner.settings.provider == "openai"
                assert owner.settings.model == base.model
                assert owner.client is openai
                assert owner.decisions_client.provider == "openai"
                assert owner.output_repair.client is openai

            task = asyncio.create_task(old_turn(llm))
            await entered.wait()
            settings.set_provider_route(route())
            with settings.pin_provider_route():
                assert settings.provider == "gemini"
                assert llm.client is gemini
                assert llm.decisions_client.provider == "gemini"
                assert llm.output_repair.client is gemini
            resume.set()
            await task
            settings.reset("provider")
            assert llm.client is openai
            await llm.close()
            openai.close.assert_awaited_once()
            gemini.close.assert_awaited_once()

    asyncio.run(run())

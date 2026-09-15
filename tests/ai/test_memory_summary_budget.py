from types import SimpleNamespace as NS
from unittest.mock import AsyncMock

import pytest

from hina_bot.ai.runtime_llm import LLM, SHARED_SUMMARY_POLICY, SUMMARY_POLICY
from hina_bot.core.config import Settings


class FakeStore:
    def __init__(self):
        self.saved_summary = None
        self.saved_shared = None

    def pending(self, scope):
        return [{
            "id": 1,
            "created_at": "2026-09-15 00:00:00",
            "content": "기억할 내용",
            "reply": "응답",
        }]

    def summary(self, scope):
        return "", 0

    def save_summary(self, scope, text, through):
        self.saved_summary = (text, through)

    def pending_shared(self, scope):
        return [{
            "id": 2,
            "created_at": "2026-09-15 00:00:00",
            "name": "사용자",
            "content": "공개 직접 호출",
        }]

    def shared_summary(self, scope):
        return "", 0

    def save_shared_summary(self, scope, name, text, through):
        self.saved_shared = (name, text[:1500], through)


def _summary_llm(output_text: str):
    llm = object.__new__(LLM)
    llm.settings = NS(
        summary_every=1,
        memory_model="memory-model",
        memory_output_tokens=4096,
    )
    llm.memory_client = object()
    llm.usage = NS(request=AsyncMock(return_value=NS(
        status="completed",
        output_text=output_text,
    )))
    return llm


def test_memory_summary_policies_keep_personal_and_shared_limits_separate():
    assert "개인 장기 기억을 한국어 1800자 이내" in SUMMARY_POLICY
    assert "장기 기억을 한국어 1200자 이내" in SHARED_SUMMARY_POLICY
    assert "다른 대화에서 공개 참고 문맥" in SHARED_SUMMARY_POLICY


@pytest.mark.asyncio
async def test_personal_summary_uses_memory_generation_budget_and_2000_char_storage_cap():
    llm = _summary_llm("x" * 2500)
    store = FakeStore()
    scope = NS(guild_id=None, user_id=100)

    await llm.summarize(store, scope)

    request = llm.usage.request.await_args.kwargs
    assert request["model"] == "memory-model"
    assert request["max_output_tokens"] == 4096
    assert request["instructions"] == SUMMARY_POLICY
    assert store.saved_summary == ("x" * 2000, 1)


@pytest.mark.asyncio
async def test_shared_summary_uses_same_generation_budget_but_shared_policy():
    llm = _summary_llm("y" * 1800)
    store = FakeStore()
    scope = NS(guild_id=1, user_id=100)

    await llm.summarize_shared(store, scope)

    request = llm.usage.request.await_args.kwargs
    assert request["model"] == "memory-model"
    assert request["max_output_tokens"] == 4096
    assert request["instructions"] == SHARED_SUMMARY_POLICY
    assert store.saved_shared == ("사용자", "y" * 1500, 2)


def _load_settings(monkeypatch, tmp_path, memory_tokens: str | None):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("DISCORD_TOKEN", "token")
    monkeypatch.setenv("OPENAI_API_KEY", "key")
    monkeypatch.setenv("LLM_PROVIDER", "openai")
    monkeypatch.setenv("LLM_MODEL", "gpt-4.1-mini")
    monkeypatch.setenv("MEMORY_PROVIDER", "")
    monkeypatch.setenv("MEMORY_MODEL", "")
    if memory_tokens is None:
        monkeypatch.delenv("MEMORY_MAX_OUTPUT_TOKENS", raising=False)
    else:
        monkeypatch.setenv("MEMORY_MAX_OUTPUT_TOKENS", memory_tokens)
    return Settings.load()


def test_memory_generation_budget_defaults_to_4096_and_can_be_overridden(monkeypatch, tmp_path):
    assert _load_settings(monkeypatch, tmp_path, None).memory_output_tokens == 4096
    assert _load_settings(monkeypatch, tmp_path, "6144").memory_output_tokens == 6144


@pytest.mark.parametrize("value", ["127", "65537"])
def test_memory_generation_budget_rejects_out_of_range_values(monkeypatch, tmp_path, value):
    with pytest.raises(ValueError):
        _load_settings(monkeypatch, tmp_path, value)

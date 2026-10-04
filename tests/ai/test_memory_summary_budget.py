from types import SimpleNamespace as NS
from unittest.mock import AsyncMock

import pytest

from hina_bot.ai.memory_summary import normalize_memory_output
from hina_bot.ai.runtime_llm import LLM, SHARED_SUMMARY_POLICY
from hina_bot.core.config import Settings


class FakeStore:
    def __init__(self):
        self.saved_shared = None

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
        model="chat-model",
        model_routing_mode="fixed",
        memory_routing_smart_threshold=2.0,
        memory_output_tokens=4096,
        gemini_thinking_level="low",
        provider="openai",
    )
    llm.client = object()
    llm.usage = NS(request=AsyncMock(return_value=NS(
        status="completed",
        output_text=output_text,
    )))
    return llm


@pytest.mark.asyncio
async def test_shared_summary_uses_memory_generation_budget():
    llm = _summary_llm("y" * 1800)
    store = FakeStore()
    scope = NS(guild_id=1, user_id=100)

    await llm.summarize_shared(store, scope)

    request = llm.usage.request.await_args.kwargs
    assert request["model"] == "chat-model"
    assert request["max_output_tokens"] == 4096
    assert request["instructions"] == SHARED_SUMMARY_POLICY
    assert request["route_metadata"]["model_tier"] == "fixed"
    assert store.saved_shared == ("사용자", "y" * 1500, 2)


def _load_settings(monkeypatch, tmp_path, memory_tokens: str | None):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("DISCORD_TOKEN", "token")
    monkeypatch.setenv("OPENAI_API_KEY", "key")
    monkeypatch.setenv("LLM_PROVIDER", "openai")
    monkeypatch.setenv("LLM_MODEL", "gpt-4.1-mini")
    if memory_tokens is None:
        monkeypatch.delenv("MEMORY_MAX_OUTPUT_TOKENS", raising=False)
    else:
        monkeypatch.setenv("MEMORY_MAX_OUTPUT_TOKENS", memory_tokens)
    return Settings.load()


def test_memory_generation_budget_defaults_to_4096_and_can_be_overridden(monkeypatch, tmp_path):
    assert _load_settings(monkeypatch, tmp_path, None).memory_output_tokens == 4096
    assert _load_settings(monkeypatch, tmp_path, "6144").memory_output_tokens == 6144


def test_structured_memory_cadence_defaults_to_four_and_can_be_overridden(
    monkeypatch, tmp_path,
):
    assert _load_settings(monkeypatch, tmp_path, None).structured_memory_every == 4
    monkeypatch.setenv("STRUCTURED_MEMORY_EVERY", "3")
    assert _load_settings(monkeypatch, tmp_path, None).structured_memory_every == 3


def test_structured_memory_cadence_is_independent_from_shared_summary_cadence(
    monkeypatch, tmp_path,
):
    monkeypatch.setenv("STRUCTURED_MEMORY_EVERY", "9")
    monkeypatch.setenv("SUMMARY_EVERY", "8")
    assert _load_settings(monkeypatch, tmp_path, None).structured_memory_every == 9


def test_structured_memory_cadence_cannot_exceed_history_retention(monkeypatch, tmp_path):
    monkeypatch.setenv("STRUCTURED_MEMORY_EVERY", "13")
    with pytest.raises(ValueError):
        _load_settings(monkeypatch, tmp_path, None)


def test_structured_memory_stale_sweep_defaults_and_overrides(monkeypatch, tmp_path):
    defaults = _load_settings(monkeypatch, tmp_path, None)
    assert defaults.structured_memory_stale_after_seconds == 8 * 60 * 60
    assert defaults.structured_memory_sweep_interval_seconds == 60 * 60

    monkeypatch.setenv("STRUCTURED_MEMORY_STALE_AFTER_SECONDS", "7200")
    monkeypatch.setenv("STRUCTURED_MEMORY_SWEEP_INTERVAL_SECONDS", "300")
    overridden = _load_settings(monkeypatch, tmp_path, None)
    assert overridden.structured_memory_stale_after_seconds == 7200
    assert overridden.structured_memory_sweep_interval_seconds == 300


def test_structured_memory_stale_sweep_can_be_disabled(monkeypatch, tmp_path):
    monkeypatch.setenv("STRUCTURED_MEMORY_SWEEP_INTERVAL_SECONDS", "0")
    value = _load_settings(monkeypatch, tmp_path, None)
    assert value.structured_memory_sweep_interval_seconds == 0


@pytest.mark.parametrize(
    ("variable", "value"),
    [
        ("STRUCTURED_MEMORY_STALE_AFTER_SECONDS", "59"),
        ("STRUCTURED_MEMORY_STALE_AFTER_SECONDS", str(7 * 24 * 60 * 60 + 1)),
        ("STRUCTURED_MEMORY_SWEEP_INTERVAL_SECONDS", "59"),
        ("STRUCTURED_MEMORY_SWEEP_INTERVAL_SECONDS", str(24 * 60 * 60 + 1)),
    ],
)
def test_structured_memory_stale_sweep_rejects_out_of_range_values(
    monkeypatch, tmp_path, variable, value,
):
    monkeypatch.setenv(variable, value)
    with pytest.raises(ValueError):
        _load_settings(monkeypatch, tmp_path, None)


@pytest.mark.parametrize("value", ["127", "65537"])
def test_memory_generation_budget_rejects_out_of_range_values(monkeypatch, tmp_path, value):
    with pytest.raises(ValueError):
        _load_settings(monkeypatch, tmp_path, value)


def test_removed_memory_provider_and_model_env_are_ignored(monkeypatch, tmp_path):
    monkeypatch.setenv("MEMORY_PROVIDER", "gemini")
    monkeypatch.setenv("MEMORY_MODEL", "legacy-memory-model")
    value = _load_settings(monkeypatch, tmp_path, None)
    assert not hasattr(value, "memory_provider")
    assert not hasattr(value, "memory_model")


@pytest.mark.asyncio
@pytest.mark.parametrize("output,status,clears", [
    ("  <NO_MEMORY>\n", "completed", True),
    ("없음", "completed", True),
    ("없습니다.", "completed", True),
    ("기억할 내용 없음", "completed", True),
    ("기억할 정보가 없습니다.", "completed", True),
    ("저장할 내용 없음", "completed", True),
    ("", "completed", False),
    ("   ", "completed", False),
    ("<NO_MEMORY>", "incomplete", False),
])
async def test_shared_empty_memory_advances_cursor_only_for_explicit_completed_result(
    output, status, clears,
):
    from hina_bot.core.routing import Scope
    from hina_bot.core.store import Store

    llm = _summary_llm(output)
    llm.usage.request.return_value.status = status
    store = Store(":memory:")
    scope = Scope(1, 2, 100, public_at_capture=True)
    try:
        store.add_shared_call(scope, 10, "A", "안녕")
        store.save_shared_summary(scope, "A", "이전 기억", 0)
        await llm.summarize_shared(store, scope)
        saved = store.shared_summary(scope)
        pending = store.pending_shared(scope)
        assert saved == (("", 1) if clears else ("이전 기억", 0))
        assert len(pending) == (0 if clears else 1)
    finally:
        store.close()


@pytest.mark.asyncio
async def test_marker_inside_real_shared_memory_is_not_treated_as_empty():
    text = "사용자는 <NO_MEMORY>라는 문자열을 처리하는 프로그램을 개발 중이다."
    llm = _summary_llm(text)
    store = FakeStore()
    await llm.summarize_shared(store, NS(guild_id=1, user_id=100))
    assert store.saved_shared == ("사용자", text, 2)


@pytest.mark.parametrize("text", [
    "사용자는 '없음'을 답변 형식으로 사용한다.",
    "기억할 내용 없음이라는 문구를 처리하는 프로그램을 개발 중이다.",
])
def test_empty_memory_phrases_inside_real_memory_are_preserved(text):
    assert normalize_memory_output(text) == text

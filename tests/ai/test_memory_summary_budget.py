from types import SimpleNamespace as NS
from unittest.mock import AsyncMock

import pytest

from hina_bot.ai.memory_summary import normalize_memory_output
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


def test_memory_summary_policies_keep_personal_and_shared_limits_separate():
    assert "개인 장기 기억을 한국어 1800자 이내" in SUMMARY_POLICY
    assert "장기 기억을 한국어 1200자 이내" in SHARED_SUMMARY_POLICY
    assert "공개 참고 문맥으로 사용될 수 있으므로" in SHARED_SUMMARY_POLICY
    assert "같은 말투·역할극 요청이 여러 번 나와도" in SUMMARY_POLICY
    assert "'앞으로', '항상', '평소에도'" in SUMMARY_POLICY


@pytest.mark.asyncio
async def test_personal_summary_uses_shared_fixed_model_and_memory_generation_budget():
    llm = _summary_llm("x" * 2500)
    store = FakeStore()
    scope = NS(guild_id=None, user_id=100)

    await llm.summarize(store, scope)

    request = llm.usage.request.await_args.kwargs
    assert request["model"] == "chat-model"
    assert request["max_output_tokens"] == 4096
    assert request["instructions"] == SUMMARY_POLICY
    assert request["route_metadata"]["model_tier"] == "fixed"
    assert store.saved_summary == ("x" * 2000, 1)


@pytest.mark.asyncio
async def test_shared_summary_uses_same_model_pool_and_shared_policy():
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


def test_structured_memory_cadence_cannot_exceed_summary_cadence(monkeypatch, tmp_path):
    monkeypatch.setenv("STRUCTURED_MEMORY_EVERY", "9")
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
@pytest.mark.parametrize("shared", [False, True])
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
async def test_empty_memory_advances_cursor_only_for_explicit_completed_result(
    shared, output, status, clears,
):
    from hina_bot.core.routing import Scope
    from hina_bot.core.store import Store

    llm = _summary_llm(output)
    llm.usage.request.return_value.status = status
    store = Store(":memory:")
    scope = Scope(1 if shared else None, 2, 100, public_at_capture=shared)
    try:
        if shared:
            store.add_shared_call(scope, 10, "A", "안녕")
            store.save_shared_summary(scope, "A", "이전 기억", 0)
            await llm.summarize_shared(store, scope)
            saved = store.shared_summary(scope)
            pending = store.pending_shared(scope)
        else:
            store.add(scope, 10, "안녕", "반가워")
            store.save_summary(scope, "이전 기억", 0)
            await llm.summarize(store, scope)
            saved = store.summary(scope)
            pending = store.pending(scope)
        assert saved == (("", 1) if clears else ("이전 기억", 0))
        assert len(pending) == (0 if clears else 1)
    finally:
        store.close()


@pytest.mark.asyncio
async def test_marker_inside_real_memory_is_not_treated_as_empty():
    text = "사용자는 <NO_MEMORY>라는 문자열을 처리하는 프로그램을 개발 중이다."
    llm = _summary_llm(text)
    store = FakeStore()
    await llm.summarize(store, NS(guild_id=None, user_id=100))
    assert store.saved_summary == (text, 1)


@pytest.mark.parametrize("text", [
    "사용자는 '없음'을 답변 형식으로 사용한다.",
    "기억할 내용 없음이라는 문구를 처리하는 프로그램을 개발 중이다.",
])
def test_empty_memory_phrases_inside_real_memory_are_preserved(text):
    assert normalize_memory_output(text) == text

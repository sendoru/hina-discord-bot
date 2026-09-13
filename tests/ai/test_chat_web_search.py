import json
from dataclasses import replace

import httpx
import pytest
from openai import AsyncOpenAI

from hina_bot.chat_llm import LLM
from hina_bot.config import Settings
from hina_bot.lore import LoreIndex
from hina_bot.routing import Scope
from hina_bot.store import Store
from hina_bot.web_bot import LLM as DiscordLLM


@pytest.fixture
async def chat_llm():
    calls = []

    def handler(request):
        calls.append(json.loads(request.content))
        return httpx.Response(200, json={
            "id": "resp_test",
            "object": "response",
            "created_at": 0,
            "status": "completed",
            "model": "test-model",
            "output": [{
                "type": "message",
                "id": "msg_test",
                "role": "assistant",
                "status": "completed",
                "content": [{"type": "output_text", "text": "응.", "annotations": []}],
            }],
        })

    client = AsyncOpenAI(
        api_key="test-not-a-real-key",
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )
    llm = LLM(Settings("test", "test", model="test-model", summary_every=2,
                       usage_log_path="", chat_web_search=True), client=client)
    llm.lore = LoreIndex([])
    yield llm, calls
    await llm.close()


def test_discord_runtime_uses_chat_llm():
    assert DiscordLLM is LLM


@pytest.mark.asyncio
async def test_relation_question_requires_search_without_local_evidence(chat_llm):
    llm, calls = chat_llm
    store = Store(":memory:")
    try:
        await llm.answer(store, Scope(None, 20, 100), "사용자", "나기사 만나본 적 있어?")
        payload = calls[-1]
        assert payload["tools"] == [{"type": "web_search", "search_context_size": "low"}]
        assert payload["tool_choice"] == "required"
        assert "외부 확인" in payload["instructions"]
        assert "세계관 외부 확인" in payload["instructions"]
        assert "세계관 사실 질문" in payload["instructions"]
        assert "같은 사건에" in payload["instructions"]
    finally:
        store.close()


@pytest.mark.asyncio
async def test_relation_question_still_searches_with_one_local_fact(chat_llm):
    llm, calls = chat_llm
    llm.lore = LoreIndex([{
        "id": "test.hina.nagisa.meeting",
        "lane": "canon",
        "fact_type": "fact_direct",
        "summary": "히나는 나기사와 특정 사건에서 직접 만난 적이 있다.",
        "keywords": ["나기사", "만남"],
        "subjects": ["히나", "나기사"],
        "knowledge": "direct_experience",
        "timeline": "테스트 사건",
    }])
    store = Store(":memory:")
    try:
        await llm.answer(store, Scope(None, 20, 100), "사용자", "나기사 만나본 적 있어?")
        payload = calls[-1]
        assert payload["tool_choice"] == "required"
        assert payload["tools"][0]["search_context_size"] == "low"
        reference = json.loads(payload["input"][0]["content"].split("\n", 1)[1])
        assert reference["lore_reference"][0]["reference"] == "test.hina.nagisa.meeting"
    finally:
        store.close()


@pytest.mark.asyncio
async def test_simple_fact_with_local_world_fact_has_no_web_tool_overhead(chat_llm):
    llm, calls = chat_llm
    llm.lore = LoreIndex([{
        "id": "test.hina.weapon",
        "lane": "canon",
        "fact_type": "fact_direct",
        "summary": "히나의 기관총 이름은 종막의 디스트로이어다.",
        "keywords": ["무기", "총 이름", "종막의 디스트로이어"],
        "subjects": ["히나"],
        "knowledge": "self",
        "timeline": "상시",
    }])
    store = Store(":memory:")
    try:
        await llm.answer(store, Scope(None, 20, 100), "사용자", "총 이름 뭐야?")
        payload = calls[-1]
        assert "tools" not in payload
        assert "tool_choice" not in payload
        assert "[외부 확인]" not in payload["instructions"]
        assert "세계관 사실 질문" in payload["instructions"]
    finally:
        store.close()


@pytest.mark.asyncio
async def test_named_who_question_is_world_fact_not_self_identity(chat_llm):
    llm, calls = chat_llm
    store = Store(":memory:")
    try:
        await llm.answer(store, Scope(None, 20, 100), "사용자", "나기사 누구야?")
        payload = calls[-1]
        assert payload["tool_choice"] == "required"
        assert payload["tools"][0]["search_context_size"] == "low"
        assert "세계관 사실 질문" in payload["instructions"]
    finally:
        store.close()


@pytest.mark.asyncio
async def test_current_release_question_requires_search(chat_llm):
    llm, calls = chat_llm
    store = Store(":memory:")
    try:
        await llm.answer(store, Scope(None, 20, 100), "사용자", "한섭에 지금 어디까지 공개됐어?")
        payload = calls[-1]
        assert payload["tool_choice"] == "required"
        assert payload["tools"] == [{"type": "web_search", "search_context_size": "low"}]
        assert "현재 정보" in payload["instructions"]
    finally:
        store.close()


@pytest.mark.asyncio
async def test_clock_question_uses_runtime_context_without_search(chat_llm):
    llm, calls = chat_llm
    store = Store(":memory:")
    try:
        await llm.answer(store, Scope(None, 20, 100), "사용자", "지금 몇 시야?")
        payload = calls[-1]
        assert "tools" not in payload
        assert "tool_choice" not in payload
        assert "[현재 시점]" in payload["instructions"]
        assert "Asia/Seoul" in payload["instructions"]
    finally:
        store.close()


@pytest.mark.asyncio
async def test_location_dependent_live_question_is_optional_without_default_location(chat_llm):
    llm, calls = chat_llm
    store = Store(":memory:")
    try:
        await llm.answer(store, Scope(None, 20, 100), "사용자", "지금 날씨 어때?")
        payload = calls[-1]
        assert payload["tools"] == [{"type": "web_search", "search_context_size": "low"}]
        assert "tool_choice" not in payload
        assert "기본 지역: 설정되지 않음" in payload["instructions"]
    finally:
        store.close()


@pytest.mark.asyncio
async def test_default_location_allows_forced_live_lookup(chat_llm):
    llm, calls = chat_llm
    llm.settings = replace(llm.settings, runtime_default_location="서울")
    store = Store(":memory:")
    try:
        await llm.answer(store, Scope(None, 20, 100), "사용자", "지금 날씨 어때?")
        payload = calls[-1]
        assert payload["tool_choice"] == "required"
        assert "기본 지역: 서울" in payload["instructions"]
    finally:
        store.close()


@pytest.mark.asyncio
async def test_public_schedule_countdown_requires_live_lookup(chat_llm):
    llm, calls = chat_llm
    store = Store(":memory:")
    try:
        await llm.answer(
            store, Scope(None, 20, 100), "사용자", "이번 추석 연휴 시작까지 며칠 남았어?"
        )
        payload = calls[-1]
        assert payload["tool_choice"] == "required"
        assert payload["tools"][0]["type"] == "web_search"
        assert "현재 정보" in payload["instructions"]
    finally:
        store.close()


@pytest.mark.asyncio
async def test_ambiguous_current_fact_gets_optional_search_tool(chat_llm):
    llm, calls = chat_llm
    store = Store(":memory:")
    try:
        await llm.answer(store, Scope(None, 20, 100), "사용자", "오늘 뭐 먹지?")
        payload = calls[-1]
        assert payload["tools"] == [{"type": "web_search", "search_context_size": "low"}]
        assert "tool_choice" not in payload
    finally:
        store.close()


@pytest.mark.asyncio
async def test_self_identity_question_has_no_web_tool_overhead(chat_llm):
    llm, calls = chat_llm
    store = Store(":memory:")
    try:
        await llm.answer(store, Scope(None, 20, 100), "사용자", "너 누구야?")
        payload = calls[-1]
        assert "tools" not in payload
        assert "tool_choice" not in payload
        assert "[외부 확인]" not in payload["instructions"]
        assert "세계관 사실 질문" not in payload["instructions"]
    finally:
        store.close()


@pytest.mark.asyncio
async def test_personal_memory_question_has_no_web_tool_overhead(chat_llm):
    llm, calls = chat_llm
    store = Store(":memory:")
    try:
        await llm.answer(store, Scope(None, 20, 100), "사용자", "내 생일 기억하고 있어?")
        payload = calls[-1]
        assert "tools" not in payload
        assert "tool_choice" not in payload
        assert "[외부 확인]" not in payload["instructions"]
        assert "세계관 사실 질문" not in payload["instructions"]
    finally:
        store.close()


@pytest.mark.asyncio
async def test_general_chat_has_no_web_search_prompt_or_tool(chat_llm):
    llm, calls = chat_llm
    store = Store(":memory:")
    try:
        await llm.answer(store, Scope(None, 20, 100), "사용자", "오늘 좀 피곤하네")
        payload = calls[-1]
        assert "tools" not in payload
        assert "tool_choice" not in payload
        assert "[외부 확인]" not in payload["instructions"]
    finally:
        store.close()


@pytest.mark.asyncio
async def test_roleplay_now_question_does_not_trigger_search(chat_llm):
    llm, calls = chat_llm
    store = Store(":memory:")
    try:
        await llm.answer(store, Scope(None, 20, 100), "사용자", "지금 뭐해?")
        payload = calls[-1]
        assert "tools" not in payload
        assert "tool_choice" not in payload
    finally:
        store.close()


@pytest.mark.asyncio
async def test_web_search_can_be_disabled_in_settings(chat_llm):
    llm, calls = chat_llm
    llm.settings = replace(llm.settings, chat_web_search=False)
    store = Store(":memory:")
    try:
        await llm.answer(store, Scope(None, 20, 100), "사용자", "나기사 만나본 적 있어?")
        payload = calls[-1]
        assert "tools" not in payload
        assert "tool_choice" not in payload
        assert "[외부 확인]" not in payload["instructions"]
    finally:
        store.close()

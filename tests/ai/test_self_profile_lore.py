from types import SimpleNamespace

from hina_bot.ai.information_pipeline import LLM
from hina_bot.ai.information_routing import InformationRoute, classify_information_request
from hina_bot.ai.self_profile_lore import fallback_references
from hina_bot.core.lore import LoreIndex


def _empty_registry():
    return SimpleNamespace(search=lambda *args, **kwargs: [])


def test_birthday_question_uses_local_route_and_finds_local_fact():
    request = classify_information_request("히나야 생일 언제야?")
    assert request.route == InformationRoute.LOCAL_LORE

    llm = object.__new__(LLM)
    llm.settings = SimpleNamespace(lore_max_items=6, lore_max_chars=3200, community_lore=True)
    llm.runtime_lore = _empty_registry()
    llm.story_context = _empty_registry()
    llm.lore = LoreIndex.load()
    references = llm.lore_references("히나야 생일 언제야?")

    assert any("2월 19일" in row.get("content", "") for row in references)


def test_birthday_fallback_uses_configured_character(monkeypatch):
    monkeypatch.setenv("CHARACTER_NAME", "텐도 아리스")
    monkeypatch.setenv("CHARACTER_ALIASES", "아리스,텐도 아리스")
    monkeypatch.setenv("CHARACTER_BIRTHDAY", "3월 25일")

    references = fallback_references("텐도 아리스 생일")

    assert references == [{
        "reference": "local_profile.character.birthday",
        "kind": "world_fact",
        "content": "텐도 아리스의 생일은 3월 25일이다.",
        "awareness": "self",
        "time": "프로필 상시 설정",
    }]


def test_empty_birthday_disables_fallback(monkeypatch):
    monkeypatch.setenv("CHARACTER_BIRTHDAY", "")
    assert fallback_references("생일") == []


def test_chat_llm_does_not_offer_web_for_self_profile():
    llm = object.__new__(LLM)
    llm.settings = SimpleNamespace(chat_web_search=True, runtime_default_location="")
    assert llm._web_search_mode("히나야 생일 언제야?", []) == "none"

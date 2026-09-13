from types import SimpleNamespace

from hina_bot.ai.chat_llm import LLM
from hina_bot.ai.information_routing import InformationRoute, classify_information_request
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


def test_chat_llm_does_not_offer_web_for_self_profile():
    llm = object.__new__(LLM)
    llm.settings = SimpleNamespace(chat_web_search=True, runtime_default_location="")
    assert llm._web_search_mode("히나야 생일 언제야?", []) == "none"

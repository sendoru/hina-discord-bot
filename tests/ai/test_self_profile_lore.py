from types import SimpleNamespace

from hina_bot.ai.chat_llm import LLM
from hina_bot.ai.information_routing import InformationRoute, classify_information_request
from hina_bot.core.lore import LoreIndex


def test_birthday_question_uses_local_route_and_finds_local_fact():
    request = classify_information_request("히나야 생일 언제야?")
    assert request.route == InformationRoute.LOCAL_LORE
    references = LoreIndex.load().search(request.lore_query, limit=6, chars=3200)
    assert any("2월 19일" in row.get("content", "") for row in references)


def test_chat_llm_does_not_offer_web_for_self_profile():
    llm = object.__new__(LLM)
    llm.settings = SimpleNamespace(chat_web_search=True, runtime_default_location="")
    assert llm._web_search_mode("히나야 생일 언제야?", []) == "none"

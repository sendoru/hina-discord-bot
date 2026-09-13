from types import SimpleNamespace as NS

from hina_bot.chat_llm import LLM
from hina_bot.freshness import FreshnessMode, classify_freshness


def _llm():
    llm = object.__new__(LLM)
    llm.settings = NS(chat_web_search=True, runtime_default_location="")
    return llm


def _world_reference():
    return [{
        "kind": "world_fact",
        "reference": "canon.test.gehenna",
        "content": "게헨나는 키보토스의 학원이며 히나가 선도부장을 맡고 있다.",
        "awareness": "direct_experience",
    }]


def test_in_world_present_incident_does_not_offer_live_search():
    llm = _llm()
    content = "지금 게헨나에 사고가 터졌니"

    freshness = classify_freshness(content)
    assert freshness == FreshnessMode.AUTO
    assert llm._web_search_mode(content, _world_reference(), freshness) == "none"


def test_real_world_present_incident_keeps_optional_live_search():
    llm = _llm()
    content = "지금 서울에 사고가 터졌니"

    freshness = classify_freshness(content)
    assert freshness == FreshnessMode.AUTO
    assert llm._web_search_mode(content, [], freshness) == "auto"


def test_external_current_question_with_lore_anchor_still_allows_search():
    llm = _llm()
    content = "지금 게헨나 굿즈 판매 상황 어때?"

    freshness = classify_freshness(content)
    assert freshness == FreshnessMode.AUTO
    assert llm._web_search_mode(content, _world_reference(), freshness) == "auto"


def test_in_world_rule_requires_actual_lore_anchor():
    llm = _llm()
    content = "현재 이름 모를 학원에 무슨 일 있어?"

    freshness = classify_freshness(content)
    assert freshness == FreshnessMode.AUTO
    assert llm._web_search_mode(content, [], freshness) == "auto"

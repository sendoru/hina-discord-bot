from types import SimpleNamespace

from hina_bot.ai.information_pipeline import LLM
from hina_bot.ai.information_routing import InformationRoute, classify_information_request
from hina_bot.core.lore import LoreIndex


def _empty_registry():
    return SimpleNamespace(search=lambda *args, **kwargs: [])


def test_birthday_question_uses_local_route_and_finds_local_fact():
    request = classify_information_request("생일 언제야?")
    assert request.route == InformationRoute.LOCAL_LORE

    llm = object.__new__(LLM)
    llm.settings = SimpleNamespace(
        lore_max_items=6,
        lore_max_chars=3200,
        community_lore=True,
        call_prefixes=("히나야",),
    )
    llm.runtime_lore = _empty_registry()
    llm.story_context = _empty_registry()
    llm.lore = LoreIndex.load()
    references = llm.lore_references("생일 언제야?")

    assert any("2월 19일" in row.get("content", "") for row in references)


def test_configured_call_prefix_still_uses_local_profile_lore():
    llm = object.__new__(LLM)
    llm.settings = SimpleNamespace(
        lore_max_items=6,
        lore_max_chars=3200,
        community_lore=True,
        call_prefixes=("히나야",),
    )
    llm.runtime_lore = _empty_registry()
    llm.story_context = _empty_registry()
    llm.lore = LoreIndex.load()

    references = llm.lore_references("히나야 생일 언제야?")

    assert any("2월 19일" in row.get("content", "") for row in references)


def test_chat_llm_does_not_offer_web_for_self_profile():
    llm = object.__new__(LLM)
    llm.settings = SimpleNamespace(
        chat_web_search=True,
        runtime_default_location="",
        call_prefixes=("히나야",),
    )
    assert llm._web_search_decision("히나야 생일 언제야?", []).mode == "none"

def test_named_hina_profile_structured_evidence_suppresses_web():
    llm = object.__new__(LLM)
    llm.settings = SimpleNamespace(
        lore_max_items=6,
        lore_max_chars=3200,
        community_lore=True,
        call_prefixes=("히나야",),
        chat_web_search=True,
        runtime_default_location="",
    )
    llm.runtime_lore = _empty_registry()
    llm.story_context = _empty_registry()
    llm.lore = LoreIndex.load()

    content = "히나 생일 언제야?"
    references = llm.lore_references(content)
    assert any(row.get("reference") == "canon.hina.birthday" for row in references)
    decision = llm._web_search_decision(content, references)
    assert decision.mode == "none"
    assert decision.reason == "local_evidence_sufficient"


def test_relation_web_fallback_uses_claim_metadata_not_reference_wording():
    llm = object.__new__(LLM)
    llm.settings = SimpleNamespace(
        lore_max_items=6,
        lore_max_chars=3200,
        community_lore=True,
        call_prefixes=("히나야",),
        chat_web_search=True,
        runtime_default_location="",
    )
    llm.runtime_lore = _empty_registry()
    llm.story_context = _empty_registry()
    llm.lore = LoreIndex.load()

    content = "호시노 직접 만나본 적 있어?"
    references = llm.lore_references(content)
    assert any(
        row.get("reference") == "canon.hina.first_meeting_with_hoshino_vol1"
        for row in references
    )
    decision = llm._web_search_decision(content, references)
    assert decision.mode == "none"
    assert decision.reason == "local_evidence_sufficient"


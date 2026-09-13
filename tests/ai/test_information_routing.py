from hina_bot.ai.information_evidence import search_mode
from hina_bot.ai.information_routing import (
    InformationRoute,
    classify_information_request,
)


def fact(reference, content, awareness="direct_experience"):
    return {
        "reference": reference,
        "kind": "world_fact",
        "content": content,
        "awareness": awareness,
    }


def test_self_profile_uses_local_lore_without_web():
    request = classify_information_request("히나야 생일 언제야?")
    assert request.route == InformationRoute.LOCAL_LORE
    assert request.lore_query == "소라사키 히나 생일"
    assert request.world_fact_question
    assert search_mode(request, [], enabled=True) == "none"


def test_omitted_self_subject_is_canonicalized():
    request = classify_information_request("오늘 키 몇이야?")
    assert request.route == InformationRoute.LOCAL_LORE
    assert request.lore_query == "소라사키 히나 키"


def test_named_character_profile_can_fall_back_to_web():
    request = classify_information_request("나기사 생일 언제야?")
    assert request.route == InformationRoute.LOCAL_THEN_WEB
    assert request.lore_query == "나기사 생일 언제야?"
    assert search_mode(request, [], enabled=True) == "required"


def test_personal_memory_has_priority_over_profile_words():
    request = classify_information_request("내 생일 기억하고 있어?")
    assert request.route == InformationRoute.MEMORY
    assert search_mode(request, [], enabled=True) == "none"


def test_live_real_world_question_uses_web():
    request = classify_information_request("오늘 코스피 얼마야?")
    assert request.route == InformationRoute.WEB
    assert not request.world_fact_question
    assert search_mode(request, [], enabled=True) == "required"


def test_relation_query_uses_matching_local_event_evidence():
    request = classify_information_request("나기사 직접 만나본 적 있어?")
    refs = [fact("canon.hina.nagisa.meeting", "히나는 나기사와 직접 만난 적이 있다.")]
    assert request.route == InformationRoute.LOCAL_THEN_WEB
    assert "만남" in request.lore_query
    assert search_mode(request, refs, enabled=True) == "none"


def test_relation_query_rejects_unrelated_profile_fact():
    request = classify_information_request("나기사 직접 만나본 적 있어?")
    refs = [fact("canon.nagisa.profile", "나기사는 티파티의 호스트다.", "public_knowledge")]
    assert search_mode(request, refs, enabled=True) == "required"


def test_ambiguous_temporal_question_only_offers_search():
    request = classify_information_request("오늘 뭐 먹지?")
    assert request.route == InformationRoute.GENERAL
    assert search_mode(request, [], enabled=True) == "auto"

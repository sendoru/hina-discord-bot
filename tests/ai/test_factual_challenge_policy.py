from types import SimpleNamespace

from hina_bot.ai.contextual_routing import needs_context_grounding
from hina_bot.ai.information_pipeline import InformationPipeline
from hina_bot.ai.information_routing import InformationRoute, classify_information_request
from hina_bot.ai.rp_output_policy import (
    FACTUAL_CHALLENGE_QUERY,
    ProvenanceMode,
    provenance_mode,
)
from hina_bot.ai.runtime_llm import GENERAL_RP_OUTPUT_POLICY


def _pipeline(*, web_search: bool = True):
    llm = object.__new__(InformationPipeline)
    llm.settings = SimpleNamespace(chat_web_search=web_search)
    return llm


def test_factual_challenge_patterns_cover_common_corrections():
    for text in (
        "그거 맞아?",
        "그건 확실해?",
        "아닌데?",
        "틀린 거 아니야?",
        "아까 네가 말한 거 잘못된 거 아니야?",
    ):
        assert FACTUAL_CHALLENGE_QUERY.search(text), text


def test_factual_challenge_is_grounded_without_becoming_route_inheritance():
    assert needs_context_grounding("아닌데?")
    assert needs_context_grounding("틀린 거 아니야?")
    assert not needs_context_grounding("왜 하늘은 파란색이야?")


def test_general_factual_challenge_offers_optional_verification():
    request = classify_information_request("그거 맞아?")
    assert request.route == InformationRoute.GENERAL
    assert request.factual_challenge
    assert _pipeline()._web_search_mode("그거 맞아?", []) == "auto"


def test_explicit_pisyeol_request_requires_search_and_shows_source():
    request = classify_information_request("17은 어디 피셜이지?")
    assert request.route == InformationRoute.WEB
    assert request.explicit_source
    assert _pipeline()._web_search_mode("17은 어디 피셜이지?", []) == "required"
    assert provenance_mode("17은 어디 피셜이지?", web_search=True) == ProvenanceMode.EXPLICIT_SOURCE


def test_web_disabled_still_keeps_challenge_local():
    assert _pipeline(web_search=False)._web_search_mode("그거 맞아?", []) == "none"


def test_correction_policy_forbids_invented_defenses_without_blindly_accepting_user():
    policy = GENERAL_RP_OUTPUT_POLICY
    assert "기존 주장을 지키려고" in policy
    assert "이유·규칙·출처·사건을 새로 만들지" in policy
    assert "사용자의 반박도 자동으로 사실로 받아들이지" in policy
    assert "뒷받침되지 않으면 짧게 인정하고 정정" in policy
    assert "캐릭터의 체면보다 정확성을 우선" in policy

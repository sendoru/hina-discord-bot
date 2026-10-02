from types import SimpleNamespace

from hina_bot.ai.contextual_routing import needs_context_grounding
from hina_bot.ai.information_pipeline import InformationPipeline
from hina_bot.ai.information_routing import InformationRoute, classify_information_request
from hina_bot.ai.rp_output_policy import (
    FACTUAL_CHALLENGE_QUERY,
    ProvenanceMode,
    provenance_mode,
)

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
    assert _pipeline()._web_search_decision("그거 맞아?", []).mode == "auto"


def test_explicit_pisyeol_request_requires_search_and_shows_source():
    request = classify_information_request("17은 어디 피셜이지?")
    assert request.route == InformationRoute.WEB
    assert request.explicit_source
    assert _pipeline()._web_search_decision("17은 어디 피셜이지?", []).mode == "required"
    assert provenance_mode("17은 어디 피셜이지?", web_search=True) == ProvenanceMode.EXPLICIT_SOURCE


def test_web_disabled_still_keeps_challenge_local():
    assert _pipeline(web_search=False)._web_search_decision("그거 맞아?", []).mode == "none"



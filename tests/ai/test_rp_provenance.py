from types import SimpleNamespace

from hina_bot.ai.information_pipeline import InformationPipeline
from hina_bot.ai.rp_output_policy import (
    ProvenanceMode,
    hide_web_citations,
    provenance_mode,
)


def test_provenance_modes_follow_actual_search_state():
    assert provenance_mode("일반 질문", web_search=False) == ProvenanceMode.SILENT
    assert provenance_mode("출처 어디야?", web_search=False) == ProvenanceMode.SILENT
    assert provenance_mode("최신 정보", web_search=True) == ProvenanceMode.NATURAL_LOOKUP
    assert provenance_mode("일리움 링크스킬", web_search=True) == ProvenanceMode.NATURAL_LOOKUP


def test_provider_citations_are_always_hidden():
    assert hide_web_citations(ProvenanceMode.SILENT)
    assert hide_web_citations(ProvenanceMode.NATURAL_LOOKUP)


def test_source_request_is_left_open_for_semantic_routing():
    llm = object.__new__(InformationPipeline)
    llm.settings = SimpleNamespace(chat_web_search=True)
    decision = llm._web_search_decision("그거 출처 어디야?", [])
    assert decision.mode == "none"
    assert decision.locked is False
    assert decision.reason == "semantic_open"


def test_personal_context_does_not_offer_external_search():
    llm = object.__new__(InformationPipeline)
    llm.settings = SimpleNamespace(chat_web_search=True)
    assert llm._web_search_decision("내 생일 기억하고 있어?", []).mode == "none"

from types import SimpleNamespace

from hina_bot.ai.information_pipeline import InformationPipeline
from hina_bot.ai.rp_output_policy import (
    ProvenanceMode,
    hide_web_citations,
    provenance_instruction,
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


def test_natural_lookup_instruction_delegates_source_display_to_model():
    text = provenance_instruction(ProvenanceMode.NATURAL_LOOKUP)
    assert "자연스러울 때 조회 사실을 짧게 언급해도 됩니다" in text
    assert "현재 사용자의 실제 요청 의미를 직접 판단하세요" in text
    assert "특정 표현을 습관적으로 반복" in text
    assert "잠깐 확인해봤는데" not in text
    assert "자료를 좀 확인해보니까" not in text


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

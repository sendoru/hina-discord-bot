from types import SimpleNamespace

from hina_bot.ai.information_pipeline import InformationPipeline
from hina_bot.ai.rp_output_policy import (
    ProvenanceMode,
    hide_web_citations,
    provenance_instruction,
    provenance_mode,
)


def test_provenance_modes():
    assert provenance_mode("일반 질문", web_search=False) == ProvenanceMode.SILENT
    assert provenance_mode("최신 정보", web_search=True) == ProvenanceMode.NATURAL_LOOKUP
    assert provenance_mode("출처 어디야?", web_search=True) == ProvenanceMode.EXPLICIT_SOURCE


def test_citations_are_only_visible_for_explicit_source_requests():
    assert hide_web_citations(ProvenanceMode.SILENT)
    assert hide_web_citations(ProvenanceMode.NATURAL_LOOKUP)
    assert not hide_web_citations(ProvenanceMode.EXPLICIT_SOURCE)


def test_natural_lookup_instruction_defaults_to_direct_answer():
    text = provenance_instruction(ProvenanceMode.NATURAL_LOOKUP)
    assert "답변 내용으로 바로 시작하세요" in text
    assert "조회 사실 자체가 답변에 중요할 때만" in text
    assert "잠깐 확인해봤는데" not in text
    assert "자료를 좀 확인해보니까" not in text


def test_source_request_forces_web_search_when_enabled():
    llm = object.__new__(InformationPipeline)
    llm.settings = SimpleNamespace(chat_web_search=True)
    assert llm._web_search_decision("그거 출처 어디야?", []).mode == "required"


def test_personal_context_does_not_offer_external_search():
    llm = object.__new__(InformationPipeline)
    llm.settings = SimpleNamespace(chat_web_search=True)
    assert llm._web_search_decision("내 생일 기억하고 있어?", []).mode == "none"

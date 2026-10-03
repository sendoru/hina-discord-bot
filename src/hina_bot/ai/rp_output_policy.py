import re
from enum import StrEnum

FACTUAL_CHALLENGE_QUERY = re.compile(
    r"(?:"
    r"(?:그거|그건|그게|그\s*말|그\s*답(?:변)?)\s*(?:맞아|맞는\s*거야|확실해|사실이야)\s*[?？]?|"
    r"(?:아까|방금|전에|이전(?:에|의)?|네가\s*말한|네\s*답(?:변)?)\s*.{0,40}"
    r"(?:맞아|확실해|틀린|잘못|아니야|아닌데)|"
    r"(?:^|[\s,])(?:아닌데|틀린\s*(?:거|것)?\s*아니야?|잘못된\s*(?:거|것)?\s*아니야?)\s*[?？]?$"
    r")",
    re.IGNORECASE,
)


class ProvenanceMode(StrEnum):
    SILENT = "silent"
    NATURAL_LOOKUP = "natural_lookup"


def provenance_mode(_content: str, *, web_search: bool) -> ProvenanceMode:
    """Choose provenance guidance from actual retrieval state, not keyword heuristics."""
    if web_search:
        return ProvenanceMode.NATURAL_LOOKUP
    return ProvenanceMode.SILENT


def provenance_instruction(mode: ProvenanceMode) -> str:
    if mode == ProvenanceMode.NATURAL_LOOKUP:
        return (
            "[자연스러운 외부 확인]\n"
            "이번 답변에서는 외부 확인을 했습니다. 검색 사실을 매번 같은 도입부로 알릴 필요는 없습니다. "
            "질문 내용상 히나가 원래 알기 어려운 최신·외부 정보를 확인한 맥락이라면, 답변 흐름에 "
            "자연스러울 때 조회 사실을 짧게 언급해도 됩니다. 다만 특정 표현을 습관적으로 반복하거나 "
            "검색 도구 자체를 메타적으로 설명하지 마세요. 출처·링크·사이트명·인용 표시가 필요한지는 "
            "현재 사용자의 실제 요청 의미를 직접 판단하세요. 사용자가 근거나 출처를 원하면 필요한 "
            "범위에서 실제 확인한 출처를 짧게 덧붙일 수 있고, 그렇지 않으면 드러내지 마세요."
        )
    return (
        "[출처 비노출]\n"
        "참고자료·기억·RAG 같은 내부 정보 획득 과정은 말하지 말고, 답변에 필요한 내용만 자연스럽게 "
        "사용하세요."
    )


def hide_web_citations(_mode: ProvenanceMode) -> bool:
    """Hide provider-added citation decorations; visible source text is model-authored."""
    return True

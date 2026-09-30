import re
from enum import StrEnum

SOURCE_REQUEST_QUERY = re.compile(
    r"(?:출처|근거\s*(?:링크|주소)?|링크|소스|피셜|어디서\s*(?:봤|찾았|알았|확인))",
    re.IGNORECASE,
)

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
    EXPLICIT_SOURCE = "explicit_source"


def provenance_mode(content: str, *, web_search: bool) -> ProvenanceMode:
    """Choose how retrieval provenance may appear in the RP response."""
    if SOURCE_REQUEST_QUERY.search(content):
        return ProvenanceMode.EXPLICIT_SOURCE
    if web_search:
        return ProvenanceMode.NATURAL_LOOKUP
    return ProvenanceMode.SILENT


def provenance_instruction(mode: ProvenanceMode) -> str:
    if mode == ProvenanceMode.EXPLICIT_SOURCE:
        return (
            "[출처 표시]\n"
            "사용자가 출처나 근거를 직접 물었습니다. 답변 자체는 히나의 말투와 1인칭을 유지하고, "
            "필요한 범위에서만 실제 확인한 출처나 링크를 짧게 덧붙일 수 있습니다. 검색 과정이나 "
            "도구 사용법을 설명하지 마세요."
        )
    if mode == ProvenanceMode.NATURAL_LOOKUP:
        return (
            "[자연스러운 외부 확인]\n"
            "이번 답변에서는 외부 확인을 했습니다. 외부 확인은 답변을 위한 참고 과정일 뿐이므로, "
            "사용자가 확인 과정 자체를 묻지 않았다면 조회 사실을 상투적인 도입부로 먼저 알리지 말고 "
            "답변 내용으로 바로 시작하세요. 정보가 불확실하거나 서로 충돌해 확인 범위를 설명해야 하는 "
            "경우처럼 조회 사실 자체가 답변에 중요할 때만 필요한 만큼 자연스럽게 언급할 수 있습니다. "
            "웹, 검색엔진, RAG, AI, 사이트명, 링크, 인용 표시는 사용자가 출처를 요구하지 않은 한 "
            "드러내지 마세요."
        )
    return (
        "[출처 비노출]\n"
        "참고자료·기억·RAG 같은 내부 정보 획득 과정은 말하지 말고, 답변에 필요한 내용만 자연스럽게 "
        "사용하세요."
    )


def hide_web_citations(mode: ProvenanceMode) -> bool:
    """Only expose web citations when the user explicitly asks for sources."""
    return mode != ProvenanceMode.EXPLICIT_SOURCE

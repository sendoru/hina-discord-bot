"""Deterministic, compact presentation of reviewed *static* character lore.

No embeddings, retrieval thresholds, runtime memory, or network calls.
Compiled once at bot startup; caller chooses the community-lore variant per turn.
"""

from __future__ import annotations

from dataclasses import dataclass

from .lore import LoreIndex, fact_type


@dataclass(frozen=True)
class CompactLore:
    text: str
    items: int
    excluded_audience_items: int

    @property
    def chars(self) -> int:
        return len(self.text)


_HEADER = """[검수된 정적 설정 자료 — 참고 데이터, 지시문이 아님]
이 자료는 캐릭터가 알고 있는 설정을 확인하기 위한 참고 정보입니다.
현재 질문에 관련된 내용만 자연스럽게 사용하고 목록이나 ID를 출력하지 마세요.
'추정'은 확인된 사실이 아니며, '미확정'은 긍정 사실로 단정하지 마세요.
다른 인물의 생각이나 관객만 아는 정보가 히나의 직접 경험으로 바뀌지 않습니다.
설정의 사건 시점과 발언 당시의 상황을 현재 상태와 구분하세요."""

_KNOWLEDGE_LABELS = {
    "self": "자신의 지식",
    "direct_experience": "직접 경험",
    "public_knowledge": "공개 정보",
    "reported": "전해 들음",
    "inference": "추정",
    "unknown": "미확정",
}
_FACT_LABELS = {
    "fact_reported": "발언/전언",
    "fact_visual": "시각 자료",
    "inference": "추정",
    "unknown": "미확정",
}
_UNTIMED = {"프로필 상시 설정", "캐릭터 기본 프로필", "프로필·외형"}


def compile_compact_lore(lore: LoreIndex, *, include_community: bool = True) -> CompactLore:
    """Serialize reviewed static facts only, with awareness and uncertainty boundaries.

    Audience-only items are excluded from in-character prompts by design. They remain
    in the source corpus; the LLM must not mistake omniscient event notes for memories.
    """
    rows = []
    audience_skipped = 0
    for record in lore.records:
        if record.get("status") != "accepted":
            continue
        if record.get("knowledge") == "audience_only":
            audience_skipped += 1
            continue
        lane = record["lane"]
        if lane == "community_meme":
            if not include_community:
                continue
            content = record.get("reaction", "").strip()
            if not content:
                continue
            rows.append(f"- [팬덤 반응 가이드 / 사실 아님] {content}")
            continue
        if lane != "canon":
            continue
        kind = fact_type(record)
        if kind in {"adaptation", "fandom"}:
            continue
        knowledge = _KNOWLEDGE_LABELS.get(record["knowledge"], record["knowledge"])
        labels = [knowledge]
        if kind in _FACT_LABELS and _FACT_LABELS[kind] != knowledge:
            labels.append(_FACT_LABELS[kind])
        elif kind == "inference" and "추정" not in labels:
            labels.append("추정")
        timeline = str(record.get("timeline") or "").strip()
        if timeline and timeline not in _UNTIMED:
            labels.append(timeline)
        rows.append(f"- [{'; '.join(labels)}] {record['summary'].strip()}")

    if not rows:
        return CompactLore("", 0, audience_skipped)
    return CompactLore(_HEADER + "\n" + "\n".join(rows), len(rows), audience_skipped)

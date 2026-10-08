"""Opt-in exact grounding, independent of lexical/semantic ranking and factual slots."""

from collections.abc import Iterable
from importlib.resources import files

from .knowledge_retrieval import KnowledgeCandidate, KnowledgeUsage, RankedKnowledgeCandidate
from .lore import LoreIndex
from .retrieval_v2 import RetrievalRequest

# Consumption policy for the future v2 context assembler, not a character-specific ban.
RELATION_CONTEXT_POLICY = (
    "학년·학교·소속은 각각 주어진 근거대로 사용합니다. 나이·직책·강함·존경이나 "
    "타교 학생의 학년 차이를 선후배 관계 또는 호칭으로 변환하지 않습니다. "
    "확인된 호칭은 화자→대상 방향과 시점을 따르고, 확인되지 않은 호칭은 이름으로 "
    "대신합니다. 관계 자료가 없다는 것은 관계가 없다는 증거가 아닙니다."
)


class RelationshipGrounder:
    """Index explicitly reviewed relation rows by canonical entity set.

    Single-entity rows are profile/background evidence only. Multi-entity rows must
    match the complete required pair: shared Hina metadata cannot admit unrelated
    character facts. Nothing derives seniority/addressing from profile content.
    This selects grounding, not an answer-sufficiency verdict or timeline resolver.
    """

    def __init__(self, candidates: Iterable[KnowledgeCandidate]):
        self._by_entities: dict[frozenset[str], list[tuple[int, KnowledgeCandidate]]] = {}
        for order, candidate in enumerate(candidates):
            if not self._eligible(candidate):
                continue
            self._by_entities.setdefault(frozenset(candidate.entities), []).append((order, candidate))

    @staticmethod
    def _eligible(candidate: KnowledgeCandidate) -> bool:
        return (
            KnowledgeUsage.RELATION in candidate.retrieval_usages
            and bool(candidate.entities)
            and candidate.lane == "canon"
            and candidate.confidence in {"verified", "official_secondary", "crosschecked"}
            and candidate.kr_release == "confirmed"
            and bool(candidate.source_metadata)
            and candidate.awareness != "audience_only"
            and candidate.fact_type in {
                "fact_direct", "fact_visual", "fact_reported", "unknown", "inference",
            }
            and (candidate.fact_type != "inference" or bool(candidate.evidence_ids))
        )

    @classmethod
    def load(cls, lore: LoreIndex | None = None) -> "RelationshipGrounder":
        """Reuse lore schema; supplemental rows never enter default legacy lore search."""
        supplemental = LoreIndex.load(str(files("hina_bot").joinpath(
            "data/relationship_grounding.jsonl",
        )))
        base = lore if lore is not None else LoreIndex.load()
        return cls([*supplemental.candidates(), *base.candidates()])

    def ground(
        self, request: RetrievalRequest,
    ) -> tuple[RankedKnowledgeCandidate, ...]:
        pair = frozenset(request.relation_pair or ())
        if not pair:
            return ()
        profiles = [
            row
            for entity in sorted(pair)
            for row in self._by_entities.get(frozenset((entity,)), ())
        ]
        pairs = self._by_entities.get(pair, ())
        # Exact pair evidence precedes singleton background. Packing is centralized in
        # BundleComposer, so grounding returns every admitted row in stable priority order.
        ordered = [*pairs, *profiles]
        selected = []
        seen = set()
        for order, candidate in ordered:
            if candidate.candidate_id in seen:
                continue
            selected.append(RankedKnowledgeCandidate(1.0, order, candidate))
            seen.add(candidate.candidate_id)
        return tuple(selected)

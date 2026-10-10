"""Provider-neutral v2 contracts, packing and bundle composition.

This remains opt-in. Legacy production packing/web fallback stay intact until #316.
"""

import json
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum

from .knowledge_retrieval import (
    KnowledgeCandidate,
    KnowledgeUsage,
    RankedKnowledgeCandidate,
    rank_lexical_candidates,
)


class RetrievalIntent(StrEnum):
    CONVERSATION = "conversation"
    PROFILE = "profile"
    RELATIONSHIP = "relationship"
    EVENT = "event"
    # The existing deterministic classifier does not distinguish these two purposes.
    RELATIONSHIP_OR_EVENT = "relationship_or_event"
    FACT = "fact"


@dataclass(frozen=True)
class RetrievalRequest:
    """Authorized turn inputs; not a log record or an embedding provider request.

    entities contains canonical ids resolved for the current retrieval context.
    relation_pair is a complete, unambiguous pair used only by exact relationship
    grounding. Factual/ambient retrievers must not interpret it as a generic candidate
    filter. anchor_text must come from the existing causal/egress routing policy.
    """

    visible_text: str
    retrieval_text: str
    anchor_text: str = ""
    anchor_source: str = ""
    entities: tuple[str, ...] = ()
    relation_pair: tuple[str, str] | None = None
    intent: RetrievalIntent = RetrievalIntent.CONVERSATION

    def __post_init__(self) -> None:
        if self.relation_pair is None:
            return
        if len(set(self.relation_pair)) != 2:
            raise ValueError("relation_pair must contain two distinct canonical entities")
        if not set(self.relation_pair) <= set(self.entities):
            raise ValueError("relation_pair must be present in entities")


@dataclass(frozen=True)
class UsageBudget:
    """Pure packing limits for one retrieval KnowledgeUsage."""

    max_items: int
    max_chars: int

    def __post_init__(self) -> None:
        if self.max_items < 0 or self.max_chars < 0:
            raise ValueError("usage budgets must be non-negative")


@dataclass(frozen=True)
class KnowledgeBundle:
    """Selected evidence, separated by retrieval purpose.

    Facts may include explicit lookup interpretations/unknown guards. They are not
    promoted to official facts. Ambient items shape a response only when scene-relevant;
    they need not be mentioned and current direct evidence takes precedence. Reactions
    are optional behavior guides, not factual evidence. Bundle membership alone never
    proves local evidence sufficient (#315).
    """

    facts: tuple[RankedKnowledgeCandidate, ...] = ()
    relations: tuple[RankedKnowledgeCandidate, ...] = ()
    character_insights: tuple[RankedKnowledgeCandidate, ...] = ()
    reactions: tuple[RankedKnowledgeCandidate, ...] = ()

    def __post_init__(self) -> None:
        for usage, rows in (
            (KnowledgeUsage.FACTUAL, self.facts),
            (KnowledgeUsage.RELATION, self.relations),
            (KnowledgeUsage.AMBIENT, self.character_insights),
            (KnowledgeUsage.REACTION, self.reactions),
        ):
            if any(usage not in row.candidate.retrieval_usages for row in rows):
                raise ValueError(f"candidate is not eligible for {usage}")

    def context_sections(self) -> dict[str, list[dict]]:
        """Explicit serialization boundary; never serialize the request or query here."""
        return {
            name: [row.candidate.reference_item() for row in getattr(self, name)]
            for name in ("facts", "relations", "character_insights", "reactions")
        }


CandidateIdentity = tuple[str, str]


def candidate_identity(row: RankedKnowledgeCandidate) -> CandidateIdentity:
    return row.candidate.source, row.candidate.candidate_id


def reference_size(row: RankedKnowledgeCandidate) -> int:
    return len(json.dumps(row.candidate.reference_item(), ensure_ascii=False))


def pack_ranked(
    ranked: Iterable[RankedKnowledgeCandidate],
    budget: UsageBudget,
    *,
    excluded: frozenset[CandidateIdentity] = frozenset(),
) -> tuple[RankedKnowledgeCandidate, ...]:
    """Pack already-admitted rows; score thresholds belong to retrievers, not budgets."""
    if budget.max_items == 0 or budget.max_chars == 0:
        return ()
    rows = []
    used_chars = 0
    seen = set(excluded)
    for row in ranked:
        identity = candidate_identity(row)
        if identity in seen:
            continue
        size = reference_size(row)
        if used_chars + size > budget.max_chars:
            continue
        rows.append(row)
        seen.add(identity)
        used_chars += size
        if len(rows) >= budget.max_items:
            break
    return tuple(rows)


_SECTION_ORDER = (
    KnowledgeUsage.RELATION,
    KnowledgeUsage.FACTUAL,
    KnowledgeUsage.AMBIENT,
    KnowledgeUsage.REACTION,
)
_SECTION_FIELD = {
    KnowledgeUsage.FACTUAL: "facts",
    KnowledgeUsage.RELATION: "relations",
    KnowledgeUsage.AMBIENT: "character_insights",
    KnowledgeUsage.REACTION: "reactions",
}


class BundleComposer:
    """Single packing/deduplication boundary for all retrieval purposes.

    More specific relationship grounding owns a duplicate before generic factual rows;
    factual evidence owns a duplicate before ambient/reaction guidance. A skipped duplicate
    does not consume a section slot, so that section can refill from its next ranked row.
    """

    def __init__(
        self,
        budgets: Mapping[KnowledgeUsage, UsageBudget],
        *,
        max_total_chars: int | None = None,
        max_total_items: int | None = None,
    ):
        if max_total_chars is not None and max_total_chars < 0:
            raise ValueError("max_total_chars must be non-negative")
        if max_total_items is not None and max_total_items < 0:
            raise ValueError("max_total_items must be non-negative")
        self.budgets = dict(budgets)
        self.max_total_chars = max_total_chars
        self.max_total_items = max_total_items

    def compose(
        self,
        rows: Mapping[KnowledgeUsage, Sequence[RankedKnowledgeCandidate]],
    ) -> KnowledgeBundle:
        selected: dict[KnowledgeUsage, tuple[RankedKnowledgeCandidate, ...]] = {
            usage: () for usage in KnowledgeUsage
        }
        claimed: set[CandidateIdentity] = set()
        total_used = 0
        total_items = 0

        for usage in _SECTION_ORDER:
            budget = self.budgets.get(usage)
            if budget is None:
                continue
            remaining_items = (
                budget.max_items
                if self.max_total_items is None
                else max(0, self.max_total_items - total_items)
            )
            remaining_chars = (
                budget.max_chars
                if self.max_total_chars is None
                else max(0, self.max_total_chars - total_used)
            )
            effective = UsageBudget(
                min(budget.max_items, remaining_items),
                min(budget.max_chars, remaining_chars),
            )
            packed = pack_ranked(
                rows.get(usage, ()),
                effective,
                excluded=frozenset(claimed),
            )
            selected[usage] = packed
            for row in packed:
                claimed.add(candidate_identity(row))
                total_used += reference_size(row)
                total_items += 1

        kwargs = {
            _SECTION_FIELD[usage]: selected[usage]
            for usage in KnowledgeUsage
        }
        return KnowledgeBundle(**kwargs)


def lexical_bundle(
    request: RetrievalRequest,
    candidates: Iterable[KnowledgeCandidate],
    *,
    budgets: Mapping[KnowledgeUsage, UsageBudget],
) -> KnowledgeBundle:
    """Compatibility adapter: lexical admission, shared v2 composition/packing."""
    ranked = rank_lexical_candidates(request.retrieval_text, candidates)
    rows = {
        usage: tuple(
            row for row in ranked
            if usage in row.candidate.retrieval_usages
        )
        for usage in KnowledgeUsage
    }
    return BundleComposer(budgets).compose(rows)

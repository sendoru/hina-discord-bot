"""Provider-neutral v2 contracts and an opt-in lexical compatibility adapter.

This is not the production selection policy. Legacy packing and web fallback stay intact.
"""

import json
from collections.abc import Iterable, Mapping
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

    entities contains resolved canonical ids only. required_entities is the evidence
    constraint (e.g. an entity pair), not a set of lexical aliases. Empty means unresolved.
    anchor_text must come from the existing causal/egress routing policy.
    """

    visible_text: str
    retrieval_text: str
    anchor_text: str = ""
    anchor_source: str = ""
    entities: tuple[str, ...] = ()
    required_entities: tuple[str, ...] = ()
    intent: RetrievalIntent = RetrievalIntent.CONVERSATION


@dataclass(frozen=True)
class UsageBudget:
    """Selection limits and threshold for one retrieval KnowledgeUsage."""

    max_items: int
    max_chars: int
    # Strict threshold in the ranker's scale; lexical parity uses > 0.
    min_score: float = 0.0


@dataclass(frozen=True)
class KnowledgeBundle:
    """Selected evidence, with independent retrieval usage budgets and optional empty slots.

    Facts may include explicit lookup interpretations/unknown guards. They are not
    promoted to official facts. Ambient items shape a response only when scene-relevant;
    they need not be mentioned and current direct evidence takes precedence. Reactions
    are optional behavior guides, not factual evidence. Relations require grounding
    policy in #312; membership alone never proves local evidence sufficient (#315).
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
        """Explicit serialization boundary; never serialize the request or query here.

        Reuse reference semantics, including interpretation/unknown guards and meme
        reaction-only output. Rich provenance and scores remain in the typed bundle.
        No flattening: ambient/reaction sections must not be used as factual evidence.
        """
        return {
            name: [row.candidate.reference_item() for row in getattr(self, name)]
            for name in ("facts", "relations", "character_insights", "reactions")
        }


def lexical_bundle(
    request: RetrievalRequest,
    candidates: Iterable[KnowledgeCandidate],
    *,
    budgets: Mapping[KnowledgeUsage, UsageBudget],
) -> KnowledgeBundle:
    """Rank once with the existing scorer, then select separately per enabled usage.

    Omitted budgets disable a retrieval usage. This adapter adds no entity resolution,
    grounding, semantic scores, insight activation or local-sufficiency policy. Callers must
    supply eligible candidates before enabling their retrieval usage budgets.
    """
    ranked = rank_lexical_candidates(request.retrieval_text, candidates)
    selected: dict[KnowledgeUsage, tuple[RankedKnowledgeCandidate, ...]] = {}
    for usage in KnowledgeUsage:
        budget = budgets.get(usage)
        rows, used = [], 0
        if budget is not None and budget.max_items > 0 and budget.max_chars > 0:
            for row in ranked:
                if usage not in row.candidate.retrieval_usages or row.score <= budget.min_score:
                    continue
                size = len(json.dumps(row.candidate.reference_item(), ensure_ascii=False))
                if used + size > budget.max_chars:
                    continue
                rows.append(row)
                used += size
                if len(rows) >= budget.max_items:
                    break
        selected[usage] = tuple(rows)
    return KnowledgeBundle(
        facts=selected[KnowledgeUsage.FACTUAL],
        relations=selected[KnowledgeUsage.RELATION],
        character_insights=selected[KnowledgeUsage.AMBIENT],
        reactions=selected[KnowledgeUsage.REACTION],
    )

"""Retrieval request and evidence bundle types for the local-evidence web-search check.

The retrieval v2 runtime that once filled these bundles was removed (#463); the
web-search decision still builds a request and judges a selected evidence bundle.
"""

from dataclasses import dataclass
from enum import StrEnum

from .knowledge_retrieval import KnowledgeUsage, RankedKnowledgeCandidate


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

"""Deterministic exact/lexical profile retrieval, separate from semantic factual search."""

from collections.abc import Sequence

from .knowledge_retrieval import (
    KnowledgeCandidate,
    KnowledgeUsage,
    RankedKnowledgeCandidate,
    rank_lexical_candidates,
)
from .retrieval_v2 import RetrievalIntent, RetrievalRequest


def rank_profile(
    request: RetrievalRequest,
    candidates: Sequence[KnowledgeCandidate],
) -> tuple[RankedKnowledgeCandidate, ...]:
    """Return positive lexical profile rows in legacy score order.

    The request builder already reduces known self-profile queries to reviewed field terms.
    No semantic call or relation-pair policy is involved.
    """
    if request.intent != RetrievalIntent.PROFILE:
        return ()
    return tuple(
        row for row in rank_lexical_candidates(request.retrieval_text, candidates)
        if KnowledgeUsage.FACTUAL in row.candidate.retrieval_usages
    )

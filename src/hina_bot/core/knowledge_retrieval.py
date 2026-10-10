"""Shared candidate model and lexical ranking for local lore retrieval."""

import json
import re
from collections.abc import Iterable
from dataclasses import dataclass
from enum import StrEnum

from .evidence_claims import EvidenceClaim


class KnowledgeUsage(StrEnum):
    """Retrieval purpose, independent of storage source and lore's canon/meme lane."""

    FACTUAL = "factual"
    RELATION = "relation"
    AMBIENT = "ambient"
    REACTION = "reaction"

_TOKEN = re.compile(r"[0-9A-Za-z가-힣]{2,}")
_LEXEME = re.compile(r"[0-9A-Za-z가-힣]+")
_STOPWORDS = {"뭐야", "알려줘", "어떻게"}
_WORD_CHAR = r"0-9A-Za-z가-힣"


def _terms(text: str) -> set[str]:
    return {
        token.casefold()
        for token in _TOKEN.findall(text)
        if token.casefold() not in _STOPWORDS
    }


def _lexemes(text: str) -> set[str]:
    return {
        token.casefold()
        for token in _LEXEME.findall(text)
        if token.casefold() not in _STOPWORDS
    }


def _contains_subject(text: str, subject: str) -> bool:
    """Match a complete subject phrase instead of a substring inside a longer token."""
    return bool(re.search(
        rf"(?<![{_WORD_CHAR}]){re.escape(subject)}(?![{_WORD_CHAR}])",
        text,
        re.IGNORECASE,
    ))


@dataclass(frozen=True)
class KnowledgeCandidate:
    """Provider-neutral retrieval row before ranking and prompt serialization."""

    candidate_id: str
    source: str
    kind: str
    content: str
    search_text: str
    subjects: tuple[str, ...]
    keywords: tuple[str, ...]
    reference: str | None = None
    awareness: str | None = None
    time: str | None = None
    metadata: tuple[tuple[str, str], ...] = ()
    subject_boundary: bool = False
    # Additive v2 metadata. None/empty means unavailable, never verified or resolved.
    lane: str | None = None
    usages: tuple[KnowledgeUsage, ...] = ()
    entities: tuple[str, ...] = ()
    fact_type: str | None = None
    confidence: str | None = None
    kr_release: str | None = None
    source_metadata: tuple[tuple[str, str], ...] = ()
    semantic_text: str | None = None
    evidence_ids: tuple[str, ...] = ()
    claims: tuple[EvidenceClaim, ...] = ()

    @property
    def retrieval_usages(self) -> tuple[KnowledgeUsage, ...]:
        if self.usages:
            return self.usages
        # Existing interpretations remain explicit lookup references, not ambient context.
        if self.kind == "optional_reaction":
            return (KnowledgeUsage.REACTION,)
        return (KnowledgeUsage.FACTUAL,)

    @property
    def semantic_representation(self) -> str:
        """Meaning text only; subjects/keywords are kept in the lexical channel."""
        return self.semantic_text if self.semantic_text is not None else self.search_text

    def reference_item(self) -> dict:
        if self.kind == "optional_reaction":
            # Keep rich provenance in the candidate, never in a model's reaction guide.
            return {"kind": self.kind, "content": self.content}
        if self.reference is None:
            item = {"kind": self.kind, "content": self.content}
        else:
            item = {
                "reference": self.reference,
                "kind": self.kind,
                "content": self.content,
            }
        if self.awareness is not None:
            item["awareness"] = self.awareness
        if self.time is not None:
            item["time"] = self.time
        for key, value in self.metadata:
            item[key] = value
        return item


@dataclass(frozen=True)
class RankedKnowledgeCandidate:
    # Ranking score in the backend's scale, not confidence or raw semantic cosine.
    score: float
    order: int
    candidate: KnowledgeCandidate


def lexical_score(
    query: str,
    candidate: KnowledgeCandidate,
    *,
    folded_query: str | None = None,
    query_terms: set[str] | None = None,
    query_lexemes: set[str] | None = None,
) -> int:
    folded = folded_query if folded_query is not None else query.casefold()
    terms = query_terms if query_terms is not None else _terms(query)
    lexemes = query_lexemes if query_lexemes is not None else _lexemes(query)

    score = 0
    for value in candidate.subjects:
        folded_value = value.casefold()
        if folded_value in _STOPWORDS:
            continue
        matched = (
            _contains_subject(folded, folded_value)
            if candidate.subject_boundary
            else folded_value in folded
        )
        if matched:
            score += 8 + min(len(folded_value), 8)

    for value in candidate.keywords:
        folded_value = value.casefold()
        if folded_value in folded:
            score += 5 + min(len(folded_value), 8)
        else:
            score += 3 * len(lexemes & _lexemes(value))

    score += 2 * len(terms & _terms(candidate.search_text))
    return score


def rank_lexical_candidates(
    query: str,
    candidates: Iterable[KnowledgeCandidate],
) -> list[RankedKnowledgeCandidate]:
    folded = query.casefold()
    terms = _terms(query)
    lexemes = _lexemes(query)
    ranked = []
    for order, candidate in enumerate(candidates):
        score = lexical_score(
            query,
            candidate,
            folded_query=folded,
            query_terms=terms,
            query_lexemes=lexemes,
        )
        if score:
            ranked.append(RankedKnowledgeCandidate(score, order, candidate))
    return sorted(ranked, key=lambda row: (-row.score, row.order))


def pack_references(
    ranked: Iterable[RankedKnowledgeCandidate],
    *,
    limit: int,
    chars: int,
) -> list[dict]:
    if limit <= 0 or chars <= 0:
        return []
    result, used = [], 0
    for row in ranked:
        item = row.candidate.reference_item()
        size = len(json.dumps(item, ensure_ascii=False))
        if used + size > chars:
            continue
        result.append(item)
        used += size
        if len(result) >= limit:
            break
    return result


def lexical_search(
    query: str,
    candidates: Iterable[KnowledgeCandidate],
    *,
    limit: int,
    chars: int,
) -> list[dict]:
    return pack_references(
        rank_lexical_candidates(query, candidates),
        limit=limit,
        chars=chars,
    )

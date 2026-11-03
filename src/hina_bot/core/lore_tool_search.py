"""Deterministic, bounded in-memory search over existing reviewed lore records.

The source JSONL is untouched. Search-only normalization supports Korean inflected
queries, spacing differences and short aliases without requiring entity metadata.
"""

from __future__ import annotations

import json
import math
import re
import unicodedata
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass

from .knowledge_retrieval import KnowledgeCandidate

_WORD = re.compile(r"[0-9a-z가-힣]+", re.IGNORECASE)
_STOP = frozenset({
    "뭐", "뭐야", "무슨", "왜", "어떻게", "알려줘", "설명해줘",
    "그리고", "그런데", "그건", "그거", "이거", "있는", "없는",
    "누구", "누구야", "관련", "설명", "대해서",
})
# Only well-established Korean postpositions; do not try to stem arbitrary verbs.
_PARTICLES = (
    "으로부터", "에게서는", "한테서는", "에서부터", "들에게서", "에게서",
    "한테서", "들한테", "들이랑", "들에게", "으로서", "으로써",
    "에서의", "까지는", "부터는", "에서는", "으로는", "이라도",
    "이라고", "이라는", "이랑", "들과", "들의", "들은", "들을",
    "에서", "에게", "한테", "처럼", "까지", "부터", "으로",
    "하고", "랑", "과", "와", "의", "은", "는", "이", "가",
    "을", "를", "에", "도", "만",
)


def _normal(text: str) -> str:
    return unicodedata.normalize("NFKC", text).casefold().strip()


def _compact(text: str) -> str:
    """Make multiword aliases tolerant of spaces and ordinary punctuation."""
    return "".join(_WORD.findall(_normal(text)))


def _strip_particle(token: str) -> str:
    # Tokens must retain at least two Hangul syllables. Avoid touching Latin
    # names and words whose final character merely looks like a postposition.
    if not re.fullmatch(r"[가-힣]+", token):
        return token
    for suffix in _PARTICLES:
        if token.endswith(suffix) and len(token) - len(suffix) >= 2:
            return token[:-len(suffix)]
    return token


def _tokens(text: str) -> tuple[str, ...]:
    tokens = []
    for word in _WORD.findall(_normal(text)):
        if len(word) < 2 or word in _STOP:
            continue
        stem = _strip_particle(word)
        if stem not in _STOP:
            tokens.append(stem)
    return tuple(tokens)


def _features(text: str) -> Counter[str]:
    features: Counter[str] = Counter()
    for token in _tokens(text):
        features["w:" + token] += 1
        if len(token) >= 3:
            for index in range(len(token) - 2):
                features["g:" + token[index:index + 3]] += 1
    return features


def _aliases(values: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(
        alias for value in values
        if (alias := _compact(value)) and len(alias) >= 2
    ))


@dataclass(frozen=True)
class _Document:
    candidate: KnowledgeCandidate
    subjects: tuple[str, ...]
    keywords: tuple[str, ...]
    features: Counter[str]
    length: int


def _document(candidate: KnowledgeCandidate) -> _Document:
    subjects = _aliases(candidate.subjects)
    keywords = _aliases(candidate.keywords)
    features = _features(
        " ".join((*candidate.subjects, *candidate.keywords, candidate.search_text))
    )
    return _Document(candidate, subjects, keywords, features, sum(features.values()))


def _is_allowed(candidate: KnowledgeCandidate, include_community: bool) -> bool:
    if candidate.awareness == "audience_only":
        return False
    if candidate.kind == "optional_reaction":
        return include_community
    if candidate.lane == "community_meme":
        return False
    return candidate.fact_type not in {"adaptation", "fandom"}


class LoreKeywordIndex:
    """Index approved lore once; read enabled runtime knowledge on every call.

    All metadata filters are policy filters, never incomplete entity-ID gates.
    """

    def __init__(self, candidates: Iterable[KnowledgeCandidate]):
        self._static = tuple(
            _document(candidate) for candidate in candidates
            if _is_allowed(candidate, include_community=True)
        )

    def search(
        self,
        queries: list[str],
        *,
        dynamic_candidates: Iterable[KnowledgeCandidate] = (),
        include_community: bool = True,
        limit: int = 4,
        max_chars: int = 6000,
    ) -> dict:
        if not isinstance(queries, list) or not 1 <= len(queries) <= 3:
            raise ValueError("queries must contain 1..3 search phrases")
        if any(not isinstance(q, str) or not 2 <= len(q.strip()) <= 90 for q in queries):
            raise ValueError("query must be a 2..90 character string")
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 5:
            raise ValueError("limit must be between 1 and 5")
        normalized = tuple(dict.fromkeys(_normal(query) for query in queries))
        if not all(_tokens(query) for query in normalized):
            raise ValueError("queries must contain searchable terms")
        query_info = tuple(
            (_compact(query), _features(query))
            for query in normalized
        )

        documents = [
            doc for doc in self._static
            if _is_allowed(doc.candidate, include_community)
        ]
        documents.extend(
            _document(candidate) for candidate in dynamic_candidates
            if _is_allowed(candidate, include_community)
        )
        count = len(documents)
        if not count:
            return {"status": "not_found", "results": []}

        # Document frequencies are per *distinct entry*, not repeated occurrences.
        # Common names (including the protagonist) must not dominate rare anchors.
        feature_df: Counter[str] = Counter()
        subject_df: Counter[str] = Counter()
        keyword_df: Counter[str] = Counter()
        for document in documents:
            feature_df.update(document.features.keys())
            subject_df.update(set(document.subjects))
            keyword_df.update(set(document.keywords))
        avg_length = max(1.0, sum(doc.length for doc in documents) / count)

        def rarity(freq: int) -> float:
            return math.log1p((count - freq + 0.5) / (freq + 0.5))

        ranked = []
        for order, document in enumerate(documents):
            scores = []
            for compact_query, query_features in query_info:
                score = 0.0
                matched_alias = False
                for alias in document.subjects:
                    if alias in compact_query:
                        matched_alias = True
                        score += (12.0 + min(len(alias), 8)) * rarity(subject_df[alias])
                for alias in document.keywords:
                    if alias in compact_query:
                        matched_alias = True
                        score += (6.0 + min(len(alias), 8)) * rarity(keyword_df[alias])

                word_hits = 0
                gram_hits = 0
                for feature in query_features:
                    tf = document.features.get(feature, 0)
                    if not tf:
                        continue
                    if feature.startswith("w:"):
                        word_hits += 1
                    else:
                        gram_hits += 1
                    # BM25 lexical contribution; cap repeated terms so a long
                    # summary cannot win merely by repeating a common name.
                    tf = min(tf, 3)
                    bm25 = tf * 2.2 / (
                        tf + 1.2 * (0.25 + 0.75 * document.length / avg_length)
                    )
                    score += (3.0 if feature.startswith("w:") else 0.35) * (
                        rarity(feature_df[feature]) * bm25
                    )
                # A single matching 3-gram is too weak to imply relevance;
                # keep alias, whole-word and multi-gram matches for recall.
                scores.append(
                    score if matched_alias or word_hits or gram_hits >= 2 else 0.0
                )
            best = max(scores)
            if best > 0:
                ranked.append((best + 0.12 * (sum(scores) - best), order, document))
        ranked.sort(key=lambda value: (-value[0], value[1]))

        results = []
        seen_ids = set()
        for _score, _order, document in ranked:
            candidate = document.candidate
            if candidate.candidate_id in seen_ids:
                continue
            item = {
                "id": candidate.candidate_id,
                "kind": candidate.kind,
                "content": candidate.content,
                "awareness": candidate.awareness,
                "timeline": candidate.time,
                "fact_type": candidate.fact_type,
                "confidence": candidate.confidence,
                "source": (
                    dict(candidate.source_metadata)
                    if candidate.source_metadata else {"type": candidate.source}
                ),
            }
            if candidate.kind == "optional_reaction":
                item["source"] = {"type": "community_reaction_not_fact"}
            # Account for the output envelope, not just individual record JSON.
            projected = {"status": "ok", "results": [*results, item]}
            if len(json.dumps(
                projected, ensure_ascii=False, separators=(",", ":"),
            )) > max_chars:
                continue
            results.append(item)
            seen_ids.add(candidate.candidate_id)
            if len(results) >= limit:
                break
        return {"status": "ok" if results else "not_found", "results": results}


__all__ = ["LoreKeywordIndex"]

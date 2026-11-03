"""On-demand, provider-neutral lore keyword search for a model-invoked local tool.

Only reviewed, in-character lore and enabled runtime knowledge are indexed. The
LLM writes a small set of search queries; this backend does no LLM or embedding calls.
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
# Generic connective/question words are not retrieval anchors.
_STOP = frozenset({
    "뭐", "뭐야", "무슨", "왜", "어떻게", "알려줘", "설명해줘",
    "그리고", "그런데", "그건", "그거", "이거", "있는", "없는",
})


def _normal(text: str) -> str:
    return unicodedata.normalize("NFKC", text).casefold().strip()


def _tokens(text: str) -> tuple[str, ...]:
    return tuple(
        word for word in _WORD.findall(_normal(text))
        if len(word) >= 2 and word not in _STOP
    )


def _features(text: str) -> Counter[str]:
    features: Counter[str] = Counter()
    for token in _tokens(text):
        features["w:" + token] += 1
        if len(token) >= 3:
            # Three-character grams tolerate Korean particles/inflections and
            # compound words without an external tokenizer or embedding model.
            for i in range(len(token) - 2):
                features["g:" + token[i:i + 3]] += 1
    return features


@dataclass(frozen=True)
class _Document:
    candidate: KnowledgeCandidate
    subjects: tuple[str, ...]
    keywords: tuple[str, ...]
    features: Counter[str]
    length: int


def _document(candidate: KnowledgeCandidate) -> _Document:
    subjects = tuple(_normal(v) for v in candidate.subjects if v.strip())
    keywords = tuple(_normal(v) for v in candidate.keywords if v.strip())
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
    """Small in-memory index; dynamic DB candidates are loaded for each query.

    Character-aware ranking uses exact aliases plus IDF-weighted words and
    trigrams. No hard entity filters: missing metadata must never hide a record.
    """

    def __init__(self, candidates: Iterable[KnowledgeCandidate]):
        self._static = tuple(
            _document(candidate)
            for candidate in candidates
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
        normalized_queries = tuple(dict.fromkeys(_normal(q) for q in queries))
        if not all(_tokens(q) for q in normalized_queries):
            raise ValueError("queries must contain searchable terms")

        documents = [
            doc for doc in self._static
            if _is_allowed(doc.candidate, include_community)
        ]
        documents += [
            _document(candidate)
            for candidate in dynamic_candidates
            if _is_allowed(candidate, include_community)
        ]
        count = len(documents)
        if not count:
            return {"status": "not_found", "results": []}

        df: Counter[str] = Counter()
        for document in documents:
            df.update(document.features.keys())
        average_length = max(1.0, sum(d.length for d in documents) / count)
        ranked = []
        for order, document in enumerate(documents):
            scores = []
            for query in normalized_queries:
                score = 0.0
                # Subject and keyword aliases are a *boost*, not a mandatory
                # gate. Matching within inflected text is intentional here.
                for alias in document.subjects:
                    if len(alias) >= 2 and alias in query:
                        score += 12.0 + min(len(alias), 8)
                for keyword in document.keywords:
                    if len(keyword) >= 2 and keyword in query:
                        score += 6.0 + min(len(keyword), 8)

                query_features = _features(query)
                for feature in query_features:
                    tf = document.features.get(feature, 0)
                    if not tf:
                        continue
                    idf = math.log1p((count - df[feature] + 0.5) / (df[feature] + 0.5))
                    bm25 = tf * 2.2 / (tf + 1.2 * (
                        0.25 + 0.75 * document.length / average_length
                    ))
                    score += (3.0 if feature.startswith("w:") else 0.35) * idf * bm25
                scores.append(score)
            best = max(scores)
            if best > 0:
                ranked.append((best + 0.15 * (sum(scores) - best), order, document))
        ranked.sort(key=lambda item: (-item[0], item[1]))

        results = []
        used = 0
        for _score, _order, document in ranked:
            candidate = document.candidate
            item = {
                "id": candidate.candidate_id,
                "kind": candidate.kind,
                "content": candidate.content,
                "awareness": candidate.awareness,
                "timeline": candidate.time,
                "fact_type": candidate.fact_type,
                "confidence": candidate.confidence,
                "source": dict(candidate.source_metadata) if candidate.source_metadata
                          else {"type": candidate.source},
            }
            if candidate.kind == "optional_reaction":
                item["source"] = {"type": "community_reaction_not_fact"}
            size = len(json.dumps(item, ensure_ascii=False, separators=(",", ":")))
            if used + size > max_chars:
                continue
            results.append(item)
            used += size
            if len(results) >= limit:
                break
        return {"status": "ok" if results else "not_found", "results": results}


__all__ = ["LoreKeywordIndex"]

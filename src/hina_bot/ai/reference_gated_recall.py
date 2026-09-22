"""Conservative one-turn recall for cross-space reference-gated factual memory."""

import re
from dataclasses import dataclass

from hina_bot.core.memory_items import (
    MemoryAccess,
    MemoryDisclosure,
    MemoryItem,
    MemoryKind,
    memory_access,
)

REFERENCE_RECALL_MAX_CANDIDATES = 24
REFERENCE_RECALL_MAX_ITEMS = 2

_REFERENCE_PATTERNS = (
    (
        "past_reference",
        re.compile(
            r"^\s*(?:그\s*)?(?:전에|예전에|저번에|지난번에)\s*"
            r"(?:내가\s*)?.{0,20}?(?:말했|말한|말하던|얘기했|얘기한|얘기하던|"
            r"이야기했|이야기한|이야기하던|알려줬|알려준|보냈|보낸|적었|적은)",
            re.IGNORECASE,
        ),
    ),
    (
        "self_reference",
        re.compile(
            r"(?:내가|제가|나는|난|저는|전)\s*.{0,28}?"
            r"(?:말했|말한|말하던|얘기했|얘기한|얘기하던|"
            r"이야기했|이야기한|이야기하던|알려줬|알려준|보냈|보낸|적었|적은)",
            re.IGNORECASE,
        ),
    ),
    (
        "dm_reference",
        re.compile(
            r"^\s*(?:DM|디엠)\s*에서\s*.{0,24}?"
            r"(?:말했|말한|얘기했|얘기한|이야기했|이야기한|보냈|보낸|적었|적은)",
            re.IGNORECASE,
        ),
    ),
    (
        "recall_question",
        re.compile(
            r"^\s*(?:(?:그거|그\s*얘기|그\s*이야기|그때)\s*)?"
            r"(?:기억나|기억해|기억하지|기억하고\s*있어)\s*[?？!！.]*\s*$",
            re.IGNORECASE,
        ),
    ),
    (
        "self_recall_question",
        re.compile(
            r"(?:내가|제가|나는|난|저는|전)\s*.{0,32}?"
            r"(?:기억나|기억해|기억하지|기억하고\s*있어)",
            re.IGNORECASE,
        ),
    ),
)

_TOKEN_RE = re.compile(r"[0-9A-Za-z가-힣]{2,}")
_PARTICLE_SUFFIXES = (
    "에서는",
    "으로는",
    "에게는",
    "한테는",
    "까지는",
    "부터는",
    "에서",
    "에게",
    "한테",
    "으로",
    "처럼",
    "보다",
    "부터",
    "까지",
    "은",
    "는",
    "을",
    "를",
    "도",
    "만",
    "와",
    "과",
    "랑",
)
_STOPWORDS = {
    "내가",
    "제가",
    "나는",
    "저는",
    "전에",
    "예전에",
    "저번에",
    "지난번에",
    "그전에",
    "아까",
    "dm",
    "디엠",
    "말했던",
    "말했어",
    "말한",
    "말하던",
    "얘기했던",
    "얘기했어",
    "얘기한",
    "얘기하던",
    "이야기했던",
    "이야기했어",
    "이야기한",
    "이야기하던",
    "기억나",
    "기억해",
    "기억하지",
    "기억하고",
    "있어",
    "있잖아",
    "있지",
    "그거",
    "그때",
    "얘기",
    "이야기",
    "관련",
    "대해서",
    "대한",
    "혹시",
    "다시",
}


@dataclass(frozen=True)
class ReferenceGatedRecallPlan:
    detected: bool
    detector_reason: str
    status: str
    candidate_count: int = 0
    relevant_count: int = 0
    selected: tuple[MemoryItem, ...] = ()
    authorization_reason: str = ""

    def context_rows(self) -> list[dict]:
        return [
            {
                "kind": item.kind.value,
                "content": item.content,
                "confidence": item.confidence,
                "authorization": self.authorization_reason,
            }
            for item in self.selected
        ]

    def provenance(self) -> dict:
        return {
            "detected": self.detected,
            "detector_reason": self.detector_reason,
            "status": self.status,
            "candidate_count": self.candidate_count,
            "relevant_count": self.relevant_count,
            "selected_item_ids": [item.id for item in self.selected],
            "authorization_reason": self.authorization_reason,
        }


def detect_explicit_self_reference(content: str) -> str:
    text = str(content or "").strip()
    if not text:
        return ""
    for reason, pattern in _REFERENCE_PATTERNS:
        if pattern.search(text):
            return reason
    return ""


def _normalize_token(token: str) -> str:
    value = token.lower()
    for suffix in _PARTICLE_SUFFIXES:
        if value.endswith(suffix) and len(value) - len(suffix) >= 2:
            value = value[:-len(suffix)]
            break
    return value


def _topic_anchors(content: str) -> tuple[str, ...]:
    anchors = []
    for raw in _TOKEN_RE.findall(str(content or "").lower()):
        token = _normalize_token(raw)
        if len(token) < 2 or token in _STOPWORDS:
            continue
        if token not in anchors:
            anchors.append(token)
    return tuple(anchors[:12])


def _matched_anchors(item: MemoryItem, anchors: tuple[str, ...]) -> tuple[str, ...]:
    text = item.content.lower()
    normalized_tokens = {
        _normalize_token(token)
        for token in _TOKEN_RE.findall(text)
    }
    return tuple(
        anchor
        for anchor in anchors
        if anchor in text
        or anchor in normalized_tokens
        or any(
            len(anchor) >= 3
            and len(token) >= 3
            and (anchor in token or token in anchor)
            for token in normalized_tokens
        )
    )


def _eligible_candidates(store, scope) -> list[MemoryItem]:
    reader = getattr(store, "reference_gated_memory_candidates", None)
    if not callable(reader):
        return []
    candidates = reader(scope, limit=REFERENCE_RECALL_MAX_CANDIDATES)
    result = []
    for item in candidates:
        if item.user_id != str(scope.user_id):
            continue
        if item.kind == MemoryKind.RELATIONSHIP:
            continue
        if item.disclosure != MemoryDisclosure.REFERENCE_GATED:
            continue
        if memory_access(item, scope) != MemoryAccess.HIDDEN:
            continue
        if (
            memory_access(item, scope, explicitly_referenced=True)
            != MemoryAccess.FULL
        ):
            continue
        result.append(item)
    return result


def plan_reference_gated_recall(
    store,
    scope,
    content: str,
    *,
    use_memory: bool,
    allow_cross_space: bool,
) -> ReferenceGatedRecallPlan:
    if not use_memory:
        return ReferenceGatedRecallPlan(False, "", "memory_disabled")
    if scope.guild_id is None:
        return ReferenceGatedRecallPlan(False, "", "owner_dm")
    if not allow_cross_space:
        return ReferenceGatedRecallPlan(False, "", "current_channel_only")

    detector_reason = detect_explicit_self_reference(content)
    if not detector_reason:
        return ReferenceGatedRecallPlan(False, "", "no_explicit_reference")

    candidates = _eligible_candidates(store, scope)
    if not candidates:
        return ReferenceGatedRecallPlan(
            True,
            detector_reason,
            "no_candidates",
        )

    anchors = _topic_anchors(content)
    if not anchors:
        return ReferenceGatedRecallPlan(
            True,
            detector_reason,
            "no_topic_anchor",
            candidate_count=len(candidates),
        )

    matched = []
    for item in candidates:
        terms = _matched_anchors(item, anchors)
        if terms:
            matched.append((item, terms))

    if not matched:
        return ReferenceGatedRecallPlan(
            True,
            detector_reason,
            "no_relevant_candidate",
            candidate_count=len(candidates),
        )

    best_count = max(len(terms) for _item, terms in matched)
    if best_count == 1 and len(matched) != 1:
        return ReferenceGatedRecallPlan(
            True,
            detector_reason,
            "ambiguous_single_anchor",
            candidate_count=len(candidates),
            relevant_count=len(matched),
        )

    if best_count == 1:
        selected = (matched[0][0],)
    else:
        best = [
            item
            for item, terms in matched
            if len(terms) == best_count
        ]
        best.sort(key=lambda item: (item.confidence, item.id), reverse=True)
        selected = tuple(best[:REFERENCE_RECALL_MAX_ITEMS])

    return ReferenceGatedRecallPlan(
        True,
        detector_reason,
        "authorized",
        candidate_count=len(candidates),
        relevant_count=len(matched),
        selected=selected,
        authorization_reason="owner_explicit_reference",
    )


__all__ = [
    "REFERENCE_RECALL_MAX_CANDIDATES",
    "REFERENCE_RECALL_MAX_ITEMS",
    "ReferenceGatedRecallPlan",
    "detect_explicit_self_reference",
    "plan_reference_gated_recall",
]

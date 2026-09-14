"""Helpers for short follow-up routing without changing the visible user message."""

import re

from .freshness import FreshnessMode, classify_freshness
from .rp_output_policy import SOURCE_REQUEST_QUERY

_FOLLOWUP = re.compile(
    r"^\s*(?:그럼|그러면|그렇다면|그래서|근데|그런데|"
    r"걔|얘|쟤|그거|그건|그게|그걸|그때|거기|그쪽|"
    r"오늘은|내일은|모레는|왜|언제|어디|누구|뭐|무엇|어떻게|얼마)",
    re.IGNORECASE,
)
_QUESTION = re.compile(
    r"(?:[?？]|뭐|무엇|왜|언제|어디|누구|어떻게|얼마|어때|어떻|있어|없어|해|야|니)\s*$",
    re.IGNORECASE,
)
_PREFIX = re.compile(r"^\s*(?:그럼|그러면|그렇다면|그래서|근데|그런데)\s*", re.IGNORECASE)
_TOPIC_PARTICLE = re.compile(
    r"^(오늘|내일|모레|날씨|기온|온도|강수|습도|예보|거기)"
    r"(?:은|는|도)(?=\s|[?？!.~]|$)"
)


def is_followup(text: str) -> bool:
    value = text.strip()
    return bool(
        value
        and len(value) <= 300
        and _FOLLOWUP.search(value)
        and _QUESTION.search(value)
    )


def find_anchor(store, scope, rows: list[dict], *, use_memory: bool) -> str:
    for row in reversed(rows):
        if row.get("context_kind") == "replied_message":
            value = str(row.get("content", "")).strip()
            if value:
                return value[:800]

    speaker = str(scope.user_id)
    for row in reversed(rows):
        if row.get("role") != "user" or row.get("context_kind") == "target_user_history":
            continue
        author = row.get("author_user_id") or row.get("user_id")
        if str(author or "") == speaker:
            value = str(row.get("content", "")).strip()
            if value:
                return value[:800]

    if use_memory:
        turns = store.history(scope)
        if turns:
            return str(turns[-1]["content"]).strip()[:800]
    return ""


def build_query(content: str, anchor: str) -> str:
    if not anchor:
        return content
    # A prior request for a citation should not make the new turn a citation request too.
    topic = SOURCE_REQUEST_QUERY.sub(" ", anchor)
    followup = _PREFIX.sub("", content.strip())
    followup = _TOPIC_PARTICLE.sub(r"\1", followup)
    suffix = "\n" + followup
    return topic[:max(0, 1400 - len(suffix))] + suffix


def merged_freshness(content: str, anchor: str) -> FreshnessMode:
    current = classify_freshness(content)
    if not anchor:
        return current
    previous = classify_freshness(anchor)
    if current == FreshnessMode.REQUIRED or previous == FreshnessMode.REQUIRED:
        return FreshnessMode.REQUIRED
    if current == FreshnessMode.CLOCK:
        return FreshnessMode.CLOCK
    if current == FreshnessMode.AUTO or previous == FreshnessMode.AUTO:
        return FreshnessMode.AUTO
    if previous == FreshnessMode.CLOCK:
        return FreshnessMode.CLOCK
    return current

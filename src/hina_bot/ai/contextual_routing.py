"""Helpers for short follow-up routing without changing the visible user message."""

import re
from dataclasses import dataclass

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

# Classifier grounding is intentionally broader than route inheritance.  These signals mean that
# the current request may need earlier conversation merely to resolve its referent; they do not mean
# that earlier text is safe to splice into the deterministic routing query.
_EXPLICIT_CONTEXT_REFERENCE = re.compile(
    r"(?:위(?:에|에서|의)?|앞(?:에서|의)?|아까|방금|이전(?:에|의)?|전에)\s*"
    r"(?:말|얘기|이야기|질문|설명|답변|내용|문맥|대화)|"
    r"(?:그|이)\s*(?:얘기|이야기|설명|질문|답변|부분|경우|내용|문맥)|"
    r"(?:연장선|이어서|이어가|이어지는|계속해서|계속해서는)",
    re.IGNORECASE,
)
_DEICTIC_CONTEXT_REFERENCE = re.compile(
    r"(?:^|[\s,])(?:그거|그건|그게|그걸|그때|거기|그쪽)(?=$|[\s,?？!.])",
    re.IGNORECASE,
)
_CONTINUATION_REFERENCE = re.compile(
    r"(?:^|[\s,])(?:그럼|그러면|그렇다면|그래서)(?=$|[\s,?？!.])",
    re.IGNORECASE,
)
_ELLIPTICAL_CONTEXT_QUESTION = re.compile(
    r"(?:^|\s)(?:왜|어떻게|언제|어디|누구|뭐|무엇|얼마)\s*[?？]?\s*$",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class RoutingAnchor:
    text: str = ""
    source: str = ""
    role: str = ""
    author_user_id: str = ""


def is_followup(text: str) -> bool:
    value = text.strip()
    return bool(
        value
        and len(value) <= 300
        and _FOLLOWUP.search(value)
        and _QUESTION.search(value)
    )


def needs_context_grounding(text: str, *, has_explicit_reply: bool = False) -> bool:
    """Return whether semantic classification should see a little prior conversation.

    This deliberately does not answer the stronger question asked by ``is_followup``: whether an
    old request should be inherited into the routing query.  Classifier context is non-authoritative
    evidence and its prompt can ignore irrelevant rows, so this gate prefers recall for explicit
    conversational references while avoiding weak topic-shift words such as ``근데`` on their own.
    """
    value = text.strip()
    if not value:
        return False
    if has_explicit_reply:
        return True
    if _EXPLICIT_CONTEXT_REFERENCE.search(value):
        return True
    if _DEICTIC_CONTEXT_REFERENCE.search(value):
        return True
    if _ELLIPTICAL_CONTEXT_QUESTION.search(value):
        return True
    return bool(
        _CONTINUATION_REFERENCE.search(value[:120])
        and _QUESTION.search(value)
    )


def find_anchor(store, scope, rows: list[dict], *, use_memory: bool) -> RoutingAnchor:
    for row in reversed(rows):
        if row.get("context_kind") == "replied_message":
            value = str(row.get("content", "")).strip()
            if value:
                author = row.get("author_user_id") or row.get("user_id")
                return RoutingAnchor(
                    value[:800],
                    "explicit_reply",
                    str(row.get("role", "")),
                    str(author or ""),
                )

    speaker = str(scope.user_id)
    for row in reversed(rows):
        if row.get("role") != "user" or row.get("context_kind") == "target_user_history":
            continue
        author = row.get("author_user_id") or row.get("user_id")
        if str(author or "") == speaker:
            value = str(row.get("content", "")).strip()
            if value:
                return RoutingAnchor(
                    value[:800], "own_prior_turn", "user", speaker
                )

    if use_memory:
        turns = store.history(scope)
        if turns:
            value = str(turns[-1]["content"]).strip()
            if value:
                return RoutingAnchor(
                    value[:800], "conversation_history", "user", speaker
                )
    return RoutingAnchor()


def find_prior_user_request(
    store,
    scope,
    rows: list[dict],
    *,
    use_memory: bool,
) -> str:
    """Return the caller's latest request independently from an explicit topic anchor."""
    speaker = str(scope.user_id)
    for row in reversed(rows):
        if row.get("role") != "user" or row.get("context_kind") == "target_user_history":
            continue
        author = row.get("author_user_id") or row.get("user_id")
        if str(author or "") != speaker:
            continue
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

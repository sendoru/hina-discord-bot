"""Conservative, embedding-free personal-memory reads.

The per-query FTS5 index contains only memories authorized for this speaker and
space. It is deliberately ephemeral: existing production SQLite schema is not
changed and newly retracted/edited memories cannot survive in a stale index.
"""

from __future__ import annotations

import re
import sqlite3
from collections.abc import Iterable

from hina_bot.core.memory_items import MemoryAccess, MemoryItem, MemoryKind, memory_access

from .local_tools import LocalToolError, LocalToolRegistry, LocalToolSpec
from .structured_memory_context import _serialize_memory_item

PROFILE_MAX_ITEMS = 5
PROFILE_MAX_CHARS = 1400
SEARCH_MAX_ITEMS = 4
SEARCH_MAX_CHARS = 2100

_PROFILE_WEIGHT = {
    MemoryKind.BOUNDARY: 4,
    MemoryKind.PREFERENCE: 3,
    MemoryKind.FACT: 2,
    MemoryKind.TASK: 1,
}
_TERMS = re.compile(r"[가-힣A-Za-z0-9_]{2,}", re.UNICODE)
_STOP_TERMS = frozenset({
    "히나", "히나야", "나한테", "내가", "나는", "너랑", "우리", "그거",
    "전에", "저번에", "지난번", "예전에", "기억나", "기억해",
    "말했던", "얘기했던", "관련", "대해서", "뭐였지", "알려줘",
})


def _authorized(items: Iterable[MemoryItem], scope) -> list[MemoryItem]:
    return [
        item for item in items
        if item.kind != MemoryKind.RELATIONSHIP
        and memory_access(item, scope) == MemoryAccess.FULL
    ]


def profile_items(items: Iterable[MemoryItem], scope) -> list[MemoryItem]:
    """A small, deterministic stable-profile projection, never raw events."""
    candidates = [
        item for item in _authorized(items, scope)
        if item.kind in _PROFILE_WEIGHT and item.confidence >= 0.8
    ]
    candidates.sort(
        key=lambda item: (
            _PROFILE_WEIGHT[item.kind],
            item.disclosure.value == "global",
            item.id,
        ),
        reverse=True,
    )
    chosen = []
    chars = 0
    for item in candidates:
        if len(chosen) >= PROFILE_MAX_ITEMS:
            break
        if chars + len(item.content) > PROFILE_MAX_CHARS:
            continue
        chosen.append(item)
        chars += len(item.content)
    return chosen


def project_selective_context(context: dict, items: Iterable[MemoryItem], scope) -> tuple[dict, tuple[int, ...]]:
    """Keep legacy relationship aggregates/authorized recall, cap personal facts."""
    chosen = profile_items(items, scope)
    result = dict(context)
    serialized = [_serialize_memory_item(item, scope) for item in chosen]
    if scope.guild_id is None:
        result["structured_owner_memory"] = serialized
    else:
        result["structured_full_memory"] = serialized
    # DM retains the numeric relationship profile, not every raw observation.
    return result, tuple(item.id for item in chosen)


def _anchors(query: str) -> list[str]:
    if not isinstance(query, str) or not 2 <= len(query.strip()) <= 160:
        raise LocalToolError("invalid_query")
    unique = []
    for term in _TERMS.findall(query.lower()):
        if term in _STOP_TERMS or term in unique:
            continue
        unique.append(term)
        if len(unique) >= 8:
            break
    if not unique:
        raise LocalToolError("missing_topic")
    return unique


def search_memory_items(items: Iterable[MemoryItem], scope, query: str) -> list[dict]:
    """FTS5 lookup over FULL-authorized current-owner facts only."""
    terms = _anchors(query)
    allowed = _authorized(items, scope)
    if not allowed:
        return []
    try:
        with sqlite3.connect(":memory:") as db:
            db.execute("CREATE VIRTUAL TABLE candidates USING fts5(content, tokenize='unicode61')")
            db.executemany(
                "INSERT INTO candidates(rowid,content) VALUES (?,?)",
                [(item.id, item.content) for item in allowed],
            )
            expression = " OR ".join('"' + term.replace('"', '""') + '"' for term in terms)
            ranked = db.execute(
                "SELECT rowid FROM candidates WHERE candidates MATCH ? "
                "ORDER BY bm25(candidates) LIMIT ?",
                (expression, SEARCH_MAX_ITEMS * 4),
            ).fetchall()
    except sqlite3.OperationalError:
        # SQLite builds without FTS5 still allow a conservative exact-term fallback.
        ranked = [
            (item.id,) for item in allowed
            if any(term in item.content.lower() for term in terms)
        ][:SEARCH_MAX_ITEMS * 4]
    by_id = {item.id: item for item in allowed}
    results = []
    chars = 0
    for (item_id,) in ranked:
        item = by_id[item_id]
        if len(results) >= SEARCH_MAX_ITEMS:
            break
        if chars + len(item.content) > SEARCH_MAX_CHARS:
            continue
        relation = (
            "current_channel"
            if item.origin_realm == scope.realm and item.origin_channel_id == str(scope.channel_id)
            else "same_server_other_channel"
            if item.origin_realm == scope.realm
            else "cross_space"
        )
        results.append({
            "id": item.id,
            "kind": item.kind.value,
            "content": item.content,
            "scope_relation": relation,
            "created_at": item.created_at,
        })
        chars += len(item.content)
    return results


SEARCH_MEMORY_TOOL = LocalToolSpec(
    name="search_memory",
    description=(
        "현재 사용자 본인이 과거에 이야기한 구체적 사실이나 사건이 필요할 때만 "
        "장기기억을 조회합니다. 대화 상대가 아닌 다른 사람의 기억은 검색할 수 없습니다. "
        "일상 대화에는 사용하지 마세요. 검색 결과가 없으면 과거 내용을 만들어내지 마세요."
    ),
    parameters={
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "과거 기억의 주제를 가리키는 구체적인 한국어/영어 검색어",
            },
        },
        "required": ["query"],
        "additionalProperties": False,
    },
)


def memory_tool_registry(
    existing: LocalToolRegistry | None,
    store,
    scope,
    *,
    on_search=None,
) -> LocalToolRegistry:
    """Make a per-request registry without modifying Discord's ambient tools."""
    registry = existing.copy() if existing is not None else LocalToolRegistry()
    calls = 0

    def search(arguments):
        nonlocal calls
        calls += 1
        if calls > 2:
            raise LocalToolError("search_limit_exceeded")
        query = arguments.get("query")
        if not isinstance(query, str):
            raise LocalToolError("invalid_query")
        reader = getattr(store, "memory_items", None)
        if not callable(reader):
            raise LocalToolError("memory_unavailable")
        result = search_memory_items(reader(scope.user_id), scope, query)
        if on_search is not None:
            on_search(len(result))
        return {"items": result, "count": len(result)}

    registry.register(SEARCH_MEMORY_TOOL, search)
    return registry

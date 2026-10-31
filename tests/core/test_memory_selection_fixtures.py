"""Run the memory v2 selection fixtures against the tiered selector (#449 §10, #457).

Personal-memory cases are executable from P2. Server memory, member profiles and
server_note cases belong to P4 and are skipped here with that reason.
"""

import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from hina_bot.core.memory_annotations import (
    Annotator,
    Audience,
    DirectiveType,
    MemoryAnnotation,
    Stability,
    TimeScope,
    UsageMode,
)
from hina_bot.core.memory_items import (
    MemoryDisclosure,
    MemoryItem,
    MemoryKind,
    MemoryStatus,
    RelationshipEvidence,
)
from hina_bot.core.memory_selection import (
    SelectionContext,
    recall_personal_memory,
    render_user_memory,
    select_personal_memory,
)
from hina_bot.core.relationship_profile import aggregate_owner_relationship_evidence
from hina_bot.core.routing import Scope

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "memory_v2" / "selection_cases.json"
DATA = json.loads(FIXTURE.read_text(encoding="utf-8"))
DEFAULTS = DATA["defaults"]
DM_CHANNEL = 9


def _user(symbol: str) -> int:
    return int(symbol.removeprefix("u"))


def _space(symbol: str | None, user_id: int) -> tuple[str, str, bool, int | None]:
    if symbol in (None, "dm"):
        return f"dm:{user_id}", str(DM_CHANNEL), False, None
    guild, channel, visibility = symbol.split(":")
    guild_id = int(guild.removeprefix("g"))
    return f"guild:{guild_id}", channel.removeprefix("c"), visibility == "public", guild_id


def _p4_only(case: dict) -> bool:
    speaker = DEFAULTS["speaker"]
    return (
        "server_memories" in case
        or "server_note" in case["expect"]
        or any(memory.get("owner", speaker) != speaker for memory in case.get("memories", ()))
    )


def _build(case: dict):
    refs = {memory["ref"]: index for index, memory in enumerate(case.get("memories", ()), 1)}
    items, annotations = [], {}
    for memory in case.get("memories", ()):
        merged = {**DEFAULTS["memory"], **memory}
        raw = {**DEFAULTS["memory"]["annotation"], **memory.get("annotation", {})}
        owner = _user(merged["owner"])
        realm, channel, public, _guild = _space(merged["origin"], owner)
        item_id = refs[memory["ref"]]
        items.append(MemoryItem(
            id=item_id,
            user_id=str(owner),
            content=merged["content"],
            kind=MemoryKind(merged["kind"]),
            origin_realm=realm,
            origin_channel_id=channel,
            origin_public_at_capture=public,
            disclosure=MemoryDisclosure(merged["disclosure"]),
            source_message_ids=(),
            confidence=merged["confidence"],
            created_at=raw["observed_at"],
            updated_at=raw["observed_at"],
            revision=merged["revision"],
            relationship_evidence=RelationshipEvidence.from_mapping(merged["relationship_evidence"]),
            status=MemoryStatus(merged["status"]),
            superseded_by=refs.get(merged["superseded_by"]),
        ))
        if not merged["annotated"]:
            continue
        annotations[item_id] = MemoryAnnotation(
            memory_item_id=item_id,
            item_revision=raw["item_revision"],
            time_scope=TimeScope(raw["time_scope"]),
            stability=Stability(raw["stability"]) if raw["stability"] else None,
            observed_at=raw["observed_at"],
            last_confirmed_at=raw["last_confirmed_at"] or raw["observed_at"],
            event_at=raw["event_at"],
            recurrence=raw["recurrence"],
            usage_mode=UsageMode(raw["usage_mode"]),
            directive_type=DirectiveType(raw["directive_type"]) if raw["directive_type"] else None,
            audience=Audience(raw["audience"]),
            importance=raw["importance"],
            slot=raw["slot"],
            keywords=tuple(raw["keywords"]),
            explicit=raw["explicit"],
            verified=raw["verified"],
            cluster_id=refs.get(raw["cluster"]),
            proactive_status=raw["proactive_status"],
            proactive_count=raw["proactive_count"],
            last_used_at=raw["last_used_at"],
            annotator=Annotator.EXTRACTOR,
            annotator_version="fixture",
        )
    return refs, items, annotations


def _context(case: dict, refs: dict[str, int]) -> SelectionContext:
    speaker = _user(DEFAULTS["speaker"])
    _realm, channel, public, guild = _space(case.get("scope", DEFAULTS["scope"]), speaker)
    context = {**DEFAULTS["context"], **case.get("context", {})}
    settings = {**DEFAULTS["settings"], **case.get("settings", {})}
    return SelectionContext(
        scope=Scope(guild, int(channel), speaker, public),
        message=case["message"],
        now=datetime.fromisoformat(DEFAULTS["now"]).astimezone(ZoneInfo(DEFAULTS["timezone"])),
        hours_since_last_turn=context["hours_since_last_turn_any_space"],
        session_followup=settings["MEMORY_SESSION_FOLLOWUP"],
        task_register=bool(context["classifier_codes"]),
        authorized_item_ids=frozenset(refs[ref] for ref in context["authorized_reference_gated"]),
    )


@pytest.mark.parametrize("case", DATA["cases"], ids=[case["id"] for case in DATA["cases"]])
def test_selection_case(case):
    if _p4_only(case):
        pytest.skip("server memory, member profile and server_note cases run in P4")
    refs, items, annotations = _build(case)
    names = {item_id: ref for ref, item_id in refs.items()}
    ctx = _context(case, refs)
    expect = case["expect"]

    if "recall" in expect:
        recall = expect["recall"]
        found = {names[entry.item.id]: entry for entry in recall_personal_memory(
            items, annotations, ctx, recall["query"]
        )}
        for ref in recall.get("include", ()):
            assert ref in found, f"recall missed {ref}"
        for ref in recall.get("exclude", ()):
            assert ref not in found, f"recall leaked {ref}"
        for ref, status in recall.get("render_status", {}).items():
            assert found[ref].status.status.value == status, ref
        return

    selection = select_personal_memory(items, annotations, ctx)
    chosen = {names[entry.item.id]: entry for entry in selection.selected}
    for ref in expect.get("include", ()):
        assert ref in chosen, f"missing {ref}; excluded={dict(selection.excluded)}"
    for ref in expect.get("exclude", ()):
        assert ref not in chosen, f"unexpected {ref} in {chosen[ref].tier}"
    for ref, tier in expect.get("tier", {}).items():
        assert chosen[ref].tier == tier, ref
    for ref, placement in expect.get("placement", {}).items():
        assert chosen[ref].placement == placement, ref
    for group in expect.get("exactly_one_of", ()):
        assert sum(ref in chosen for ref in group) == 1, group
    for ref, status in expect.get("render_status", {}).items():
        assert chosen[ref].status.status.value == status, ref
    if "relationship_profile_axes" in expect:
        profile = aggregate_owner_relationship_evidence(items, ctx.scope)
        assert sorted(profile) == sorted(expect["relationship_profile_axes"])

    rendered = render_user_memory(selection, ctx.scope)
    serialized = json.dumps(rendered, ensure_ascii=False)
    assert "confidence" not in serialized and "kind" not in serialized

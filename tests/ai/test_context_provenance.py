from types import SimpleNamespace

from hina_bot.ai.context_provenance import build_context_provenance
from hina_bot.ai.structured_memory_context import structured_memory_provenance
from hina_bot.core.memory_items import MemoryDisclosure, MemoryKind
from hina_bot.core.routing import Scope
from hina_bot.core.store import Store


def test_context_provenance_distinguishes_reference_and_direct_sources_without_content():
    scope = Scope(1, 10, 100, True)
    context = {
        "server_note": "",
        "user_note": "present but not copied",
        "personal_recent_conversation": [],
        "conversation_history": [],
        "public_server_context": [{
            "source": "guild:1:channel:20:user:200",
            "user_id": "200",
            "summary": "must not be copied",
        }],
        "channel_recent_messages": [{
            "message_id": "10",
            "role": "user",
            "author_user_id": "100",
            "content": "direct content",
            "context_kind": "speaker_thread",
            "provenance_class": "conversation",
        }],
        "active_reply_chain": [{
            "message_id": "11",
            "role": "user",
            "author_user_id": "200",
            "content": "quoted content",
            "context_kind": "reply_reference_source",
            "provenance_class": "reference_material",
            "reference_strength": "inherited_reference",
        }],
        "lore_reference": [{
            "reference": "canon.test",
            "kind": "world_fact",
            "content": "lore body must not be copied",
            "awareness": "direct_experience",
        }],
    }
    visual = SimpleNamespace(
        source="attachment",
        context_kind="replied_message",
        reference_strength="explicit_reply",
        message_id="12",
        author_user_id="200",
        author_name="Other",
        mime_type="image/png",
    )

    result = build_context_provenance(
        context,
        scope,
        egress_policy="full",
        adapter_egress={"channel_input": 3, "channel_allowed": 2, "channel_blocked": 1},
        provider_boundary={"channel_input": 2, "channel_allowed": 2, "channel_blocked": 0},
        use_memory=True,
        current_channel_only=False,
        cross_channel_memory=True,
        structured={"items": [], "relationship_axes": []},
        visuals=[visual],
    )

    by_message = {
        row["message_id"]: row
        for row in result["sources"]
        if row.get("message_id")
    }
    assert by_message["10"]["owner_relation"] == "self"
    assert by_message["10"]["is_reference"] is False
    assert by_message["11"]["owner_relation"] == "other"
    assert by_message["11"]["is_reference"] is True
    assert by_message["11"]["provenance_class"] == "reference_material"
    assert by_message["12"]["source_type"] == "visual_input"
    assert by_message["12"]["is_reference"] is True

    lore = next(row for row in result["sources"] if row["source_type"] == "lore_reference")
    assert lore["reference_id"] == "canon.test"
    assert lore["kind"] == "world_fact"

    assert "conversation_memory" not in {
        row["name"] for row in result["sections"]
    }

    serialized = repr(result)
    for secret in (
        "direct content",
        "quoted content",
        "must not be copied",
        "lore body must not be copied",
    ):
        assert secret not in serialized


def test_structured_memory_provenance_tracks_axis_local_profile_contributors():
    store = Store(":memory:")
    dm = Scope(None, 10, 100)
    guild = Scope(1, 20, 100, True)
    try:
        comfort_id = store.add_memory_item(
            dm,
            "older comfort evidence",
            kind=MemoryKind.RELATIONSHIP,
            disclosure=MemoryDisclosure.IMPLICIT,
            confidence=0.9,
            relationship_evidence={"comfort": 2},
        )
        familiarity_ids = [
            store.add_memory_item(
                dm,
                f"newer familiarity evidence {index}",
                kind=MemoryKind.RELATIONSHIP,
                disclosure=MemoryDisclosure.IMPLICIT,
                confidence=0.9,
                relationship_evidence={"familiarity": 1},
            )
            for index in range(8)
        ]

        result = structured_memory_provenance(
            store,
            guild,
            use_memory=True,
            allow_cross_space=True,
        )

        projected_ids = {
            row["item_id"]
            for row in result["items"]
            if row["projection"] == "relationship_evidence"
        }
        assert projected_ids == {comfort_id, *familiarity_ids}
        assert result["relationship_axes"] == ["comfort", "familiarity"]
    finally:
        store.close()


def test_structured_memory_provenance_matches_owner_and_relationship_projection():
    store = Store(":memory:")
    dm = Scope(None, 10, 100)
    guild = Scope(1, 20, 100, True)
    other_guild = Scope(2, 30, 100, True)
    try:
        fact_id = store.add_memory_item(
            dm,
            "owner fact",
            kind=MemoryKind.FACT,
            disclosure=MemoryDisclosure.REFERENCE_GATED,
        )
        full_fact_id = store.add_memory_item(
            guild,
            "same-space fact",
            kind=MemoryKind.FACT,
            disclosure=MemoryDisclosure.LOCAL,
        )
        full_relationship_id = store.add_memory_item(
            guild,
            "same-space relationship",
            kind=MemoryKind.RELATIONSHIP,
            disclosure=MemoryDisclosure.IMPLICIT,
            confidence=0.95,
            relationship_evidence={"familiarity": 2},
        )
        implicit_relationship_id = store.add_memory_item(
            other_guild,
            "cross-space relationship",
            kind=MemoryKind.RELATIONSHIP,
            disclosure=MemoryDisclosure.IMPLICIT,
            confidence=0.95,
            relationship_evidence={"comfort": 3},
        )

        owner = structured_memory_provenance(
            store,
            dm,
            use_memory=True,
            allow_cross_space=True,
        )
        assert {row["item_id"] for row in owner["items"]} == {
            fact_id,
            full_fact_id,
            full_relationship_id,
            implicit_relationship_id,
        }
        assert {row["projection"] for row in owner["items"]} == {"owner_dm"}
        assert owner["relationship_axes"] == ["comfort", "familiarity"]

        shared = structured_memory_provenance(
            store,
            guild,
            use_memory=True,
            allow_cross_space=True,
        )
        by_id = {row["item_id"]: row for row in shared["items"]}
        assert by_id[full_fact_id]["projection"] == "shared_full"
        assert by_id[full_fact_id]["access"] == "full"
        assert by_id[full_relationship_id]["projection"] == "relationship_full"
        assert by_id[full_relationship_id]["access"] == "full"
        assert by_id[implicit_relationship_id]["projection"] == "relationship_evidence"
        assert by_id[implicit_relationship_id]["access"] == "implicit"
        assert "comfort" in shared["relationship_axes"]
        assert fact_id not in by_id
    finally:
        store.close()

def test_context_provenance_tracks_v2_insight_sections_without_content():
    scope = Scope(1, 10, 100, True)
    context = {
        "server_note": "",
        "user_note": "",
        "personal_recent_conversation": [],
        "conversation_history": [],
        "public_server_context": [],
        "channel_recent_messages": [],
        "active_reply_chain": [],
        "lore_reference": [],
        "character_insights": [{
            "reference": "canon.insight",
            "kind": "interpretation",
            "content": "secret psychology wording",
        }],
        "optional_reactions": [{
            "reference": "canon.reaction",
            "kind": "optional_reaction",
            "content": "secret reaction wording",
        }],
    }

    result = build_context_provenance(
        context,
        scope,
        egress_policy="full",
        adapter_egress={},
        provider_boundary={},
        use_memory=False,
        current_channel_only=False,
        cross_channel_memory=False,
        structured={"items": [], "relationship_axes": []},
    )

    sections = {row["name"]: row for row in result["sections"]}
    assert sections["character_insights"]["count"] == 1
    assert sections["optional_reactions"]["count"] == 1
    assert {
        row["source_type"] for row in result["sources"]
    } >= {"character_insight", "optional_reaction"}
    serialized = repr(result)
    assert "secret psychology wording" not in serialized
    assert "secret reaction wording" not in serialized


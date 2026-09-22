import json

from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest

from hina_bot.core.config import Settings
from hina_bot.core.observability import CURRENT_TURN_ID
from hina_bot.core.routing import Scope
from hina_bot.core.store import Store
from hina_bot.discord.config_commands import ConfigCommands
from hina_bot.discord.web_bot import HinaClient


@pytest.fixture
def bot():
    store = Store(":memory:")
    client = HinaClient(
        Settings("test", "test", cooldown=0, bot_admin_ids=frozenset({100})),
        store=store,
        llm=NS(close=AsyncMock()),
    )
    yield client
    store.close()


@pytest.mark.asyncio
async def test_global_chatlog_mode_change_clears_all_recent_rows_and_hydration_state(bot):
    first = Scope(1, 10, 100)
    second = Scope(2, 20, 200)
    bot.recent.add(first, 1, "A", "ambient one")
    bot.recent.add(second, 2, "B", "ambient two")
    bot.recent.mark_hydrated(first)
    bot.recent.mark_hydrated(second)

    group = bot.tree.get_command("chatlog")
    mode = group.get_command("mode")
    interaction = NS(
        guild_id=1,
        channel_id=10,
        user=NS(id=100),
        response=NS(defer=AsyncMock()),
        followup=NS(send=AsyncMock()),
    )
    await mode.callback(group, interaction, "direct", "global")

    assert bot.recent.buffers == {}
    assert bot.recent.hydrated == set()
    assert bot.store.chat_log_enabled(first)
    assert bot.store.note("config:chatlog_capture:global") == "direct"
    assert group.get_command("capture") is None


def test_external_context_policy_hot_change_clears_all_recent_rows_and_hydration(bot):
    first = Scope(1, 10, 100)
    second = Scope(2, 20, 200)
    bot.recent.add(first, 1, "A", "ambient one")
    bot.recent.add(second, 2, "B", "ambient two")
    bot.recent.mark_hydrated(first)
    bot.recent.mark_hydrated(second)

    # Runtime entry normally wraps Settings in RuntimeSettings. The side effect itself only needs
    # the client/recent objects, so call it directly here to lock down invalidation semantics.
    ConfigCommands(bot)._apply_side_effects("external_context_policy")

    assert bot.recent.buffers == {}
    assert bot.recent.hydrated == set()


@pytest.mark.asyncio
async def test_text_identity_resolution_only_sends_live_visible_candidates():
    store = Store(":memory:")
    store.add_shared_call(Scope(1, 10, 200, True), 1, "tag : sendol", "public")
    store.add_shared_call(Scope(1, 11, 300, True), 2, "hidden-user", "old public")

    resolver = AsyncMock(return_value=NS(resolved=True, user_id="200"))
    llm = NS(close=AsyncMock(), resolve_speaker_identity=resolver)
    client = HinaClient(
        Settings("test", "test", cooldown=0, external_context_policy="full"),
        store=store,
        llm=llm,
    )
    client._connection.user = NS(id=99)

    visible = MagicMock(spec=discord.TextChannel)
    visible.permissions_for.return_value = NS(
        view_channel=True,
        read_message_history=True,
    )
    hidden = MagicMock(spec=discord.TextChannel)
    hidden.permissions_for.return_value = NS(
        view_channel=False,
        read_message_history=False,
    )
    guild = NS(
        id=1,
        default_role=NS(),
        get_channel=lambda channel_id: visible if channel_id == 10 else hidden,
        get_member=lambda user_id: (
            NS(
                id=200,
                display_name="tag : sendol",
                global_name="Sendol",
                name="sendol",
            )
            if user_id == 200
            else None
        ),
    )
    message = NS(
        guild=guild,
        author=NS(id=100),
        mentions=[],
    )
    try:
        targets, ids = await client._resolve_text_targets(
            message,
            Scope(1, 10, 100),
            "센돌이 누군지 알아?",
            strict_egress=False,
            third_party_mention=False,
        )
    finally:
        await client.close()

    assert ids == (200,)
    assert targets == [{"user_id": "200", "name": "tag : sendol"}]
    candidates = resolver.await_args.args[1]
    assert [row["user_id"] for row in candidates] == ["200"]
    assert "hidden-user" not in str(candidates)
    assert "Sendol" in candidates[0]["names"]


@pytest.mark.asyncio
async def test_strict_egress_skips_textual_cross_user_identity_resolution():
    store = Store(":memory:")
    store.add_shared_call(Scope(1, 10, 200, True), 1, "sendol", "public")
    resolver = AsyncMock()
    llm = NS(close=AsyncMock(), resolve_speaker_identity=resolver)
    client = HinaClient(
        Settings(
            "test",
            "test",
            cooldown=0,
            external_context_policy="bot_interactions_only",
        ),
        store=store,
        llm=llm,
    )
    client._connection.user = NS(id=99)
    try:
        targets, ids = await client._resolve_text_targets(
            NS(guild=NS(id=1), author=NS(id=100), mentions=[]),
            Scope(1, 10, 100),
            "센돌이 누구야?",
            strict_egress=True,
            third_party_mention=False,
        )
    finally:
        await client.close()

    assert targets == []
    assert ids == ()
    resolver.assert_not_awaited()


@pytest.mark.asyncio
async def test_identity_observability_never_logs_raw_reference_or_candidate_names(tmp_path):
    store = Store(":memory:")
    store.add_shared_call(Scope(1, 10, 200, True), 1, "tag : sendol", "public")
    resolver = AsyncMock(
        return_value=NS(
            resolved=True,
            status="resolved",
            user_id="200",
            reference="센돌",
        )
    )
    llm = NS(close=AsyncMock(), resolve_speaker_identity=resolver)
    event_path = tmp_path / "events.jsonl"
    client = HinaClient(
        Settings(
            "test",
            "deployment-secret-token",
            cooldown=0,
            external_context_policy="full",
            event_log_path=str(event_path),
        ),
        store=store,
        llm=llm,
    )
    client._connection.user = NS(id=99)

    visible = MagicMock(spec=discord.TextChannel)
    visible.permissions_for.return_value = NS(
        view_channel=True,
        read_message_history=True,
    )
    guild = NS(
        id=1,
        default_role=NS(),
        get_channel=lambda channel_id: visible,
        get_member=lambda user_id: NS(
            id=200,
            display_name="tag : sendol",
            global_name="Sendol",
            name="sendol",
        ),
    )
    message = NS(guild=guild, author=NS(id=100), mentions=[])
    token = CURRENT_TURN_ID.set("opaque-identity-turn")
    try:
        targets, ids = await client._resolve_text_targets(
            message,
            Scope(1, 10, 100),
            "센돌이 누군지 알아?",
            strict_egress=False,
            third_party_mention=False,
        )
    finally:
        CURRENT_TURN_ID.reset(token)
        await client.close()

    assert ids == (200,)
    assert targets == [{"user_id": "200", "name": "tag : sendol"}]
    raw = event_path.read_text(encoding="utf-8")
    assert "센돌" not in raw
    assert "sendol" not in raw.lower()

    row = json.loads(raw)
    assert row["event"] == "identity.resolution"
    assert row["turn_id"] == "opaque-identity-turn"
    assert row["outcome"] == "resolved"
    assert row["resolver_invoked"] is True
    assert row["candidate_count"] == 1
    assert row["raw_candidate_count"] == 1
    assert len(row["reference_group"]) == 16
    assert len(row["resolved_user_group"]) == 16
    assert row["evidence_source"] == "resolver_derived"

import json
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, MagicMock, patch

import discord
import pytest

from hina_bot.core.config import Settings
from hina_bot.core.interaction_context import CURRENT_INTERACTION_CONTEXT
from hina_bot.core.observability import current_turn_id
from hina_bot.core.routing import Scope, trigger_text
from hina_bot.core.store import Store
from hina_bot.discord.bot import BOT_TRIGGER_CHAIN_LIMIT
from hina_bot.discord.bot import HinaClient as BaseHinaClient
from hina_bot.discord.turn_provenance import build_turn_provenance
from hina_bot.discord.web_bot import HinaClient as ProductionHinaClient


def routing_message(
    text,
    *,
    author_id=300,
    bot=True,
    mentions=(),
    webhook_id=None,
    dm=False,
):
    return NS(
        content=text,
        author=NS(id=author_id, bot=bot),
        mentions=[NS(id=value) for value in mentions],
        webhook_id=webhook_id,
        guild=None if dm else NS(id=1),
        reference=None,
    )


def test_other_bots_require_an_explicit_discord_mention():
    assert trigger_text(routing_message("히나야 안녕"), 99) is None
    assert trigger_text(routing_message("히나야 안녕", dm=True), 99, True) is None
    assert trigger_text(routing_message("<@99> 안녕", mentions=(99,)), 99) == "안녕"
    assert trigger_text(routing_message("안녕 <@!99>", mentions=(99,)), 99) == "안녕"
    assert trigger_text(routing_message("<@99>", mentions=(99,)), 99) == ""


def test_self_and_webhooks_never_trigger():
    assert trigger_text(
        routing_message("<@99> 안녕", author_id=99, mentions=(99,)), 99
    ) is None
    assert trigger_text(
        routing_message("<@99> 안녕", mentions=(99,), webhook_id=123), 99
    ) is None


def test_bot_origin_is_preserved_in_turn_provenance():
    message = routing_message("<@99> 안녕", mentions=(99,))
    message.id = 10
    message.author.display_name = "다른 봇"
    provenance = build_turn_provenance(message, "안녕", [], [])
    assert provenance["origin_request"]["role"] == "bot"
    assert provenance["origin_request"]["author_user_id"] == "300"


def test_turn_provenance_keeps_message_visual_fact_without_loaded_visual_input():
    message = routing_message("<@99> 봐줘", author_id=100, bot=False, mentions=(99,))
    message.id = 15
    message.author.display_name = "사용자"
    message.attachments = [NS(content_type="image/png")]
    message.stickers = []

    provenance = build_turn_provenance(message, "봐줘", [], [])

    assert provenance["origin_request"]["has_visual"] is True


def test_turn_provenance_preserves_request_and_source_timestamps():
    message = routing_message("<@99> 이어서", author_id=100, bot=False, mentions=(99,))
    message.id = 20
    message.author.display_name = "사용자"
    message.created_at = datetime(2026, 9, 23, 5, 54, 30, tzinfo=UTC)
    replied = [{
        "message_id": "19",
        "content": "이전 답변",
        "role": "assistant",
        "user_id": "99",
        "author_user_id": "99",
        "at": "2026-09-23T05:54:20+00:00",
    }]

    provenance = build_turn_provenance(message, "이어서", replied, [])

    assert provenance["origin_request"]["at"] == "2026-09-23T05:54:30+00:00"
    assert provenance["origin_sources"][0]["at"] == "2026-09-23T05:54:20+00:00"


def test_turn_provenance_normalizes_legacy_unix_source_timestamp():
    message = routing_message("<@99> 이어서", author_id=100, bot=False, mentions=(99,))
    message.id = 20
    message.author.display_name = "사용자"
    replied = [{
        "message_id": "19",
        "content": "이전 답변",
        "role": "assistant",
        "user_id": "99",
        "author_user_id": "99",
        "unix_time": 1789999999.0,
    }]

    provenance = build_turn_provenance(message, "이어서", replied, [])

    assert provenance["origin_sources"][0]["at"].endswith("+00:00")


@pytest.fixture
async def base_client():
    store = Store(":memory:")
    llm = NS(
        answer=AsyncMock(return_value="응"),
        summarize=AsyncMock(),
        extract_structured_memory=AsyncMock(),
        summarize_shared=AsyncMock(),
        close=AsyncMock(),
    )
    tempdir = tempfile.TemporaryDirectory()
    event_path = Path(tempdir.name) / "events.jsonl"
    client = BaseHinaClient(
        Settings("test", "test", cooldown=0, event_log_path=str(event_path)),
        store=store,
        llm=llm,
    )
    client._connection.user = NS(id=99)
    channel = MagicMock(spec=discord.TextChannel)
    channel.id = 10
    channel.send = AsyncMock(return_value=NS(id=1000))
    channel.typing.return_value.__aenter__ = AsyncMock(return_value=None)
    channel.typing.return_value.__aexit__ = AsyncMock(return_value=None)
    channel.permissions_for.return_value = NS(view_channel=True, read_message_history=True)
    guild = NS(id=1, default_role=NS(), unavailable=False, me=NS(), emojis=[])
    try:
        yield client, store, llm, channel, guild, event_path
    finally:
        await client.close()
        tempdir.cleanup()


def make_message(channel, guild, *, message_id, author, text, mentions=()):
    return NS(
        id=message_id,
        content=text,
        author=author,
        guild=guild,
        channel=channel,
        mentions=[NS(id=value) for value in mentions],
        webhook_id=None,
        attachments=[],
    )


@pytest.mark.asyncio
async def test_bot_trigger_uses_recent_context_but_not_persistent_memory(base_client):
    client, store, llm, channel, guild, _, = base_client
    author = NS(
        id=300,
        bot=True,
        display_name="다른 봇",
        guild_permissions=NS(manage_guild=False),
    )
    message = make_message(
        channel,
        guild,
        message_id=1,
        author=author,
        text="<@99> 안녕",
        mentions=(99,),
    )

    await client.on_message(message)

    llm.answer.assert_awaited_once()
    assert llm.answer.call_args.kwargs["use_memory"] is False
    assert store.history(Scope(1, 10, 300)) == []
    assert store.pending_shared(Scope(1, 10, 300)) == []
    recent = client.recent.context(Scope(1, 10, 300), 9999)
    assert any(row["role"] == "bot" and row["content"] == "<@99> 안녕" for row in recent)


@pytest.mark.asyncio
async def test_consecutive_bot_trigger_guard_resets_after_human_activity(base_client):
    client, _, llm, channel, guild, event_path = base_client
    bot_author = NS(
        id=300,
        bot=True,
        display_name="다른 봇",
        guild_permissions=NS(manage_guild=False),
    )
    for message_id in range(1, BOT_TRIGGER_CHAIN_LIMIT + 2):
        await client.on_message(
            make_message(
                channel,
                guild,
                message_id=message_id,
                author=bot_author,
                text="<@99> 계속 얘기해",
                mentions=(99,),
            )
        )

    assert llm.answer.await_count == BOT_TRIGGER_CHAIN_LIMIT
    rows = [json.loads(line) for line in event_path.read_text().splitlines()]
    assert any(
        row.get("event") == "turn.dropped" and row.get("reason") == "bot_loop_guard"
        for row in rows
    )

    human = NS(
        id=100,
        bot=False,
        display_name="사용자",
        guild_permissions=NS(manage_guild=False),
    )
    await client.on_message(
        make_message(
            channel,
            guild,
            message_id=10,
            author=human,
            text="그냥 대화",
        )
    )
    await client.on_message(
        make_message(
            channel,
            guild,
            message_id=11,
            author=bot_author,
            text="<@99> 다시 질문",
            mentions=(99,),
        )
    )
    assert llm.answer.await_count == BOT_TRIGGER_CHAIN_LIMIT + 1


@pytest.mark.asyncio
async def test_production_wrapper_forwards_only_explicit_bot_calls(tmp_path):
    store = Store(":memory:")
    llm = NS(close=AsyncMock())
    event_path = tmp_path / "events.jsonl"
    client = ProductionHinaClient(
        Settings("test", "test", cooldown=0, event_log_path=str(event_path)),
        store=store,
        llm=llm,
    )
    client._connection.user = NS(id=99)
    channel = NS(id=10)
    guild = NS(id=1)
    bot_author = NS(id=300, bot=True, display_name="다른 봇")

    direct = make_message(
        channel,
        guild,
        message_id=20,
        author=bot_author,
        text="<@99> 안녕",
        mentions=(99,),
    )
    passive = make_message(
        channel,
        guild,
        message_id=21,
        author=bot_author,
        text="히나야 안녕",
    )
    forwarded_turn_ids = []
    forwarded_interactions = []

    async def capture_forwarded(_message):
        forwarded_turn_ids.append(current_turn_id())
        forwarded_interactions.append(CURRENT_INTERACTION_CONTEXT.get())

    with (
        patch("hina_bot.discord.web_bot.collect", new=AsyncMock(return_value=[])),
        patch("hina_bot.discord.web_bot.collect_reply_context", new=AsyncMock(return_value=[])),
        patch("hina_bot.discord.web_bot.collect_visual_inputs", new=AsyncMock(return_value=[])),
        patch.object(BaseHinaClient, "on_message", new=AsyncMock(side_effect=capture_forwarded)) as forwarded,
    ):
        await client.on_message(direct)
        assert forwarded.await_count == 1
        interaction = forwarded_interactions[0]
        assert interaction["speaker"]["user_id"] == "300"
        assert interaction["speaker"]["is_bot"] is True
        assert interaction["mentions"] == [{
            "user_id": "99",
            "name": "",
            "is_bot": False,
            "is_self": True,
        }]
        await client.on_message(passive)
        assert forwarded.await_count == 1

    rows = [json.loads(line) for line in event_path.read_text().splitlines()]
    preflight = [row for row in rows if row["event"] == "turn.preflight"]
    assert len(preflight) == 1
    assert preflight[0]["turn_id"] == forwarded_turn_ids[0]
    assert set(preflight[0]) >= {
        "preflight_ms",
        "identity_ms",
        "target_context_ms",
        "reply_context_ms",
        "visual_context_ms",
        "history_hydration_ms",
        "history_hydration_needed",
        "channel_context_select_ms",
        "channel_context_count",
        "visual_ref_select_ms",
        "visual_ref_count",
        "visual_fetch_ms",
        "visual_input_count",
    }

    await client.close()

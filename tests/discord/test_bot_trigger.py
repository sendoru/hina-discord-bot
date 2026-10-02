import asyncio
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
from hina_bot.discord.bot import (
    BOT_TRIGGER_CHAIN_LIMIT,
    _timed_channel_lock,
    _timed_memory_lock,
)
from hina_bot.discord.bot import HinaClient as BaseHinaClient
from hina_bot.discord.reply_context import REPLY_CONTEXT
from hina_bot.discord.target_context import TARGET_CONTEXT
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
    channel_id=10,
):
    return NS(
        content=text,
        author=NS(id=author_id, bot=bot),
        mentions=[NS(id=value) for value in mentions],
        webhook_id=webhook_id,
        guild=None if dm else NS(id=1),
        channel=NS(id=channel_id),
        reference=None,
    )


def test_other_bots_keep_existing_ping_rule_outside_always_reply_channels():
    assert trigger_text(routing_message("그냥 봇 대화"), 99) is None
    assert trigger_text(routing_message("히나야 안녕"), 99) is None
    assert trigger_text(
        routing_message("히나야 안녕"),
        99,
        always_reply_channel_ids=frozenset({10}),
    ) == "히나야 안녕"
    assert trigger_text(
        routing_message("히나야"),
        99,
        always_reply_channel_ids=frozenset({10}),
    ) == ""
    assert trigger_text(routing_message("히나야 안녕", dm=True), 99, True) is None
    assert trigger_text(
        routing_message("<@99> 안녕", mentions=(99,), dm=True), 99, True
    ) is None
    assert trigger_text(
        routing_message("<@99>", mentions=(99,), dm=True), 99, True
    ) is None
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
        Settings(discord_token="test", openai_api_key="test", cooldown=0, event_log_path=str(event_path)),
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
async def test_always_reply_channel_triggers_plain_human_messages_but_not_bot_chatter(tmp_path):
    store = Store(":memory:")
    llm = NS(
        answer=AsyncMock(return_value="응"),
        summarize=AsyncMock(),
        extract_structured_memory=AsyncMock(),
        summarize_shared=AsyncMock(),
        close=AsyncMock(),
    )
    client = BaseHinaClient(
        Settings(discord_token="test", openai_api_key="test",
            cooldown=0,
            always_reply_channel_ids=frozenset({10}),
            event_log_path=str(tmp_path / "events.jsonl"),
        ),
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

    human = NS(
        id=100,
        bot=False,
        display_name="사용자",
        guild_permissions=NS(manage_guild=False),
    )
    bot_author = NS(
        id=300,
        bot=True,
        display_name="다른 봇",
        guild_permissions=NS(manage_guild=False),
    )

    try:
        await client.on_message(
            make_message(channel, guild, message_id=1, author=human, text="그냥 대화")
        )
        await client.on_message(
            make_message(channel, guild, message_id=2, author=bot_author, text="그냥 봇 대화")
        )
        assert llm.answer.await_count == 1
    finally:
        await client.close()


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
async def test_production_wrapper_observes_guild_channel_metadata_for_passive_messages(
    tmp_path,
):
    store = Store(":memory:")
    llm = NS(close=AsyncMock())
    client = ProductionHinaClient(
        Settings(
            discord_token="test",
            openai_api_key="test",
            cooldown=0,
            event_log_path=str(tmp_path / "events.jsonl"),
        ),
        store=store,
        llm=llm,
    )
    client._connection.user = NS(id=99)
    channel = NS(id=10, name="잡담")
    guild = NS(id=1, name="히나 서버")
    bot_author = NS(id=300, bot=True, display_name="다른 봇")

    try:
        await client.on_message(
            make_message(
                channel,
                guild,
                message_id=20,
                author=bot_author,
                text="그냥 봇 대화",
            )
        )

        guild_row = store.db.execute(
            "SELECT guild_id,name FROM guild_metadata WHERE guild_id=?",
            ("1",),
        ).fetchone()
        channel_row = store.db.execute(
            "SELECT channel_id,guild_id,name FROM channel_metadata WHERE channel_id=?",
            ("10",),
        ).fetchone()
        assert tuple(guild_row) == ("1", "히나 서버")
        assert tuple(channel_row) == ("10", "1", "잡담")

        guild.name = "새 히나 서버"
        channel.name = "일반"
        await client.on_message(
            make_message(
                channel,
                guild,
                message_id=21,
                author=bot_author,
                text="여전히 그냥 봇 대화",
            )
        )

        guild_row = store.db.execute(
            "SELECT guild_id,name FROM guild_metadata WHERE guild_id=?",
            ("1",),
        ).fetchone()
        channel_row = store.db.execute(
            "SELECT channel_id,guild_id,name FROM channel_metadata WHERE channel_id=?",
            ("10",),
        ).fetchone()
        assert tuple(guild_row) == ("1", "새 히나 서버")
        assert tuple(channel_row) == ("10", "1", "일반")
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_production_wrapper_forwards_only_explicit_bot_calls(tmp_path):
    store = Store(":memory:")
    llm = NS(close=AsyncMock())
    event_path = tmp_path / "events.jsonl"
    client = ProductionHinaClient(
        Settings(discord_token="test", openai_api_key="test",
            cooldown=0,
            always_reply_channel_ids=frozenset({10}),
            event_log_path=str(event_path),
        ),
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
        text="그냥 봇 대화",
    )
    prefix_direct = make_message(
        channel,
        guild,
        message_id=22,
        author=bot_author,
        text="히나야 안녕",
    )
    dm_direct = make_message(
        channel,
        None,
        message_id=23,
        author=bot_author,
        text="<@99> DM에서 안녕",
        mentions=(99,),
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
        await client.on_message(prefix_direct)
        assert forwarded.await_count == 2
        await client.on_message(dm_direct)
        assert forwarded.await_count == 2

    rows = [json.loads(line) for line in event_path.read_text().splitlines()]
    preflight = [row for row in rows if row["event"] == "turn.preflight"]
    assert len(preflight) == 2
    assert [row["turn_id"] for row in preflight] == forwarded_turn_ids
    assert set(preflight[0]) >= {
        "preflight_ms",
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


@pytest.mark.asyncio
async def test_production_wrapper_collects_target_and_reply_context_concurrently(tmp_path):
    store = Store(":memory:")
    llm = NS(close=AsyncMock())
    client = ProductionHinaClient(
        Settings(discord_token="test", openai_api_key="test", cooldown=0, event_log_path=str(tmp_path / "events.jsonl")),
        store=store,
        llm=llm,
    )
    client._connection.user = NS(id=99)
    channel = NS(id=10)
    guild = NS(id=1)
    author = NS(id=100, bot=False, display_name="사용자", guild_permissions=NS(manage_guild=False))
    message = make_message(
        channel,
        guild,
        message_id=30,
        author=author,
        text="히나야 아까 뭐라고 했어?",
    )

    target_started = asyncio.Event()
    reply_started = asyncio.Event()

    async def collect_target(*args, **kwargs):
        target_started.set()
        await reply_started.wait()
        return []

    async def collect_reply(*args, **kwargs):
        reply_started.set()
        await target_started.wait()
        return []

    with (
        patch("hina_bot.discord.web_bot.collect", new=collect_target),
        patch("hina_bot.discord.web_bot.collect_reply_context", new=collect_reply),
        patch("hina_bot.discord.web_bot.collect_visual_inputs", new=AsyncMock(return_value=[])),
        patch.object(BaseHinaClient, "on_message", new=AsyncMock()) as forwarded,
    ):
        await asyncio.wait_for(client.on_message(message), timeout=1.0)

    assert target_started.is_set()
    assert reply_started.is_set()
    assert forwarded.await_count == 1

    rows = [
        json.loads(line)
        for line in (tmp_path / "events.jsonl").read_text().splitlines()
    ]
    preflight = next(row for row in rows if row["event"] == "turn.preflight")
    assert preflight["target_context_ms"] >= 0
    assert preflight["reply_context_ms"] >= 0

    await client.close()


@pytest.mark.asyncio
async def test_production_wrapper_preserves_target_and_reply_results_after_parallel_collection(tmp_path):
    store = Store(":memory:")
    llm = NS(close=AsyncMock())
    client = ProductionHinaClient(
        Settings(discord_token="test", openai_api_key="test", cooldown=0, event_log_path=str(tmp_path / "events.jsonl")),
        store=store,
        llm=llm,
    )
    client._connection.user = NS(id=99)
    channel = NS(id=10)
    guild = NS(id=1)
    author = NS(id=100, bot=False, display_name="사용자", guild_permissions=NS(manage_guild=False))
    message = make_message(
        channel,
        guild,
        message_id=31,
        author=author,
        text="히나야 아까 뭐라고 했어?",
    )

    target_rows = [{
        "user_id": "200",
        "name": "대상",
        "sampled_messages": [{"message_id": "target-1", "content": "target context"}],
    }]
    reply_rows = [{
        "message_id": "reply-1",
        "user_id": "100",
        "author_user_id": "100",
        "content": "reply context",
        "role": "user",
        "context_kind": "replied_message",
        "reference_strength": "explicit_reply",
    }]
    captured = {}

    async def capture_forwarded(_message):
        captured["target"] = TARGET_CONTEXT.get()
        captured["reply"] = REPLY_CONTEXT.get()

    with (
        patch("hina_bot.discord.web_bot.collect", new=AsyncMock(return_value=target_rows)),
        patch(
            "hina_bot.discord.web_bot.collect_reply_context",
            new=AsyncMock(return_value=reply_rows),
        ),
        patch("hina_bot.discord.web_bot.collect_visual_inputs", new=AsyncMock(return_value=[])),
        patch.object(BaseHinaClient, "on_message", new=AsyncMock(side_effect=capture_forwarded)),
    ):
        await client.on_message(message)

    assert captured["target"] == tuple(target_rows)
    assert captured["reply"] == tuple(reply_rows)

    await client.close()



@pytest.mark.asyncio
async def test_setup_hook_starts_llm_background_tasks(tmp_path):
    store = Store(":memory:")
    llm = NS(
        close=AsyncMock(),
        start_background_tasks=AsyncMock(),
    )
    client = BaseHinaClient(
        Settings(discord_token="test", openai_api_key="test",
            structured_memory_sweep_interval_seconds=0,
            event_log_path=str(tmp_path / "events.jsonl"),
        ),
        store=store,
        llm=llm,
    )
    client.application_info = AsyncMock(
        return_value=NS(team=None, owner=NS(id=123))
    )
    client.emoji_registry.catalog = AsyncMock(return_value=[])
    client.tree.sync = AsyncMock(return_value=[])

    await client.setup_hook()

    llm.start_background_tasks.assert_awaited_once_with()
    await client.close()



@pytest.mark.asyncio
async def test_timed_locks_record_channel_and_memory_waits_separately():
    events = []

    class Lock:
        def __init__(self, name):
            self.name = name

        async def acquire(self):
            events.append(f"acquire:{self.name}")

        def release(self):
            events.append(f"release:{self.name}")

    timings = {}
    channel_times = iter((10.0, 10.004))
    async with _timed_channel_lock(
        Lock("channel"),
        timings,
        10.0,
        clock=lambda: next(channel_times),
    ):
        events.append("channel-body")

    memory_times = iter((10.004, 10.010))
    async with _timed_memory_lock(
        Lock("memory"),
        timings,
        clock=lambda: next(memory_times),
    ):
        events.append("memory-body")

    assert events == [
        "acquire:channel",
        "channel-body",
        "release:channel",
        "acquire:memory",
        "memory-body",
        "release:memory",
    ]
    assert timings == {
        "channel_lock_wait_ms": 4,
        "lock_wait_ms": 4,
        "memory_lock_wait_ms": 6,
    }


@pytest.mark.asyncio
async def test_cooldown_uses_turn_arrival_time_after_channel_lock_backlog(tmp_path):
    store = Store(":memory:")
    llm = NS(
        answer=AsyncMock(return_value="응"),
        summarize=AsyncMock(),
        extract_structured_memory=AsyncMock(),
        summarize_shared=AsyncMock(),
        close=AsyncMock(),
    )
    client = BaseHinaClient(
        Settings(discord_token="test", openai_api_key="test",
            cooldown=0.05,
            event_log_path=str(tmp_path / "events.jsonl"),
        ),
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
    author = NS(
        id=100,
        bot=False,
        display_name="사용자",
        guild_permissions=NS(manage_guild=False),
    )
    scope = Scope(1, 10, 100)
    channel_lock = client.channel_lock(scope)
    await channel_lock.acquire()
    try:
        first = asyncio.create_task(
            client.on_message(
                make_message(
                    channel,
                    guild,
                    message_id=100,
                    author=author,
                    text="<@99> 첫 질문",
                    mentions=(99,),
                )
            )
        )
        await asyncio.sleep(0.06)
        channel_lock.release()
        await first

        await client.on_message(
            make_message(
                channel,
                guild,
                message_id=101,
                author=author,
                text="<@99> 두 번째 질문",
                mentions=(99,),
            )
        )

        assert llm.answer.await_count == 2
        rows = [
            json.loads(line)
            for line in (tmp_path / "events.jsonl").read_text().splitlines()
        ]
        assert not any(
            row.get("event") == "turn.dropped" and row.get("reason") == "cooldown"
            for row in rows
        )
    finally:
        if channel_lock.locked():
            channel_lock.release()
        await client.close()


@pytest.mark.asyncio
async def test_same_channel_next_turn_can_generate_while_prior_memory_update_runs(base_client):
    client, _, llm, channel, guild, _ = base_client
    author = NS(
        id=100,
        bot=False,
        display_name="사용자",
        guild_permissions=NS(manage_guild=False),
    )
    memory_started = asyncio.Event()
    release_memory = asyncio.Event()
    second_answer_started = asyncio.Event()
    answer_calls = 0
    extraction_calls = 0

    async def answer(*args, **kwargs):
        nonlocal answer_calls
        answer_calls += 1
        if answer_calls == 2:
            second_answer_started.set()
        return "응"

    async def extract(*args, **kwargs):
        nonlocal extraction_calls
        extraction_calls += 1
        if extraction_calls == 1:
            memory_started.set()
            await release_memory.wait()

    llm.answer.side_effect = answer
    llm.extract_structured_memory.side_effect = extract

    first = asyncio.create_task(
        client.on_message(
            make_message(
                channel,
                guild,
                message_id=200,
                author=author,
                text="<@99> 첫 질문",
                mentions=(99,),
            )
        )
    )
    await asyncio.wait_for(memory_started.wait(), timeout=1.0)

    second = asyncio.create_task(
        client.on_message(
            make_message(
                channel,
                guild,
                message_id=201,
                author=author,
                text="<@99> 두 번째 질문",
                mentions=(99,),
            )
        )
    )
    await asyncio.wait_for(second_answer_started.wait(), timeout=1.0)

    assert not first.done()
    assert llm.answer.await_count == 2

    release_memory.set()
    await asyncio.gather(first, second)

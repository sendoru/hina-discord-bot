import asyncio
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest

from hina_bot.ai.identity_resolution import SpeakerIdentityResolution
from hina_bot.core.config import Settings
from hina_bot.core.identity_context import CURRENT_RESOLVED_IDENTITIES
from hina_bot.core.routing import Scope
from hina_bot.core.store import Store
from hina_bot.discord import web_bot
from hina_bot.discord.web_bot import HinaClient, _public_context_request


def member(user_id=200, *, display_name="tag : uhe", name="2_718281", global_name="Uhe", bot=False):
    return NS(id=user_id, display_name=display_name, name=name, global_name=global_name, bot=bot)


@pytest.fixture
def runtime(tmp_path):
    target = member()
    resolver = AsyncMock(return_value=SpeakerIdentityResolution("resolved", "200", "센돌"))
    store = Store(":memory:")
    client = HinaClient(
        Settings(
            discord_token="test", external_context_policy="full",
            cooldown=0, event_log_path=str(tmp_path / "events.jsonl"),
        ),
        store=store, llm=NS(close=AsyncMock(), resolve_speaker_identity=resolver),
    )
    client._connection.user = NS(id=99)
    hidden_ids = set()
    channel = MagicMock(spec=discord.TextChannel)
    channel.id = 10
    channel.permissions_for.side_effect = lambda value: NS(
        view_channel=getattr(value, "id", 0) not in hidden_ids,
        read_message_history=True,
    )
    guild = NS(
        id=1, unavailable=False, chunked=True, members=[target], default_role=NS(id=0),
        fetch_member=AsyncMock(return_value=target), chunk=AsyncMock(), fetch_members=AsyncMock(),
    )
    guild.get_member = lambda uid: next((row for row in guild.members if row.id == uid), None)
    guild.get_channel = lambda cid: channel if cid == 10 else None
    message = NS(guild=guild, channel=channel, author=NS(id=100), mentions=[])
    yield NS(
        client=client, store=store, resolver=resolver, target=target, guild=guild,
        message=message, channel=channel, hidden_ids=hidden_ids,
    )
    store.close()


async def resolve(runtime, text, **kwargs):
    return await runtime.client._resolve_text_targets(
        runtime.message, Scope(1, 10, 100), text,
        strict_egress=kwargs.get("strict_egress", False),
        third_party_mention=kwargs.get("third_party_mention", False),
    )


@pytest.mark.parametrize("text", ["2_718281 핑해줘", "tag : uhe 불러줘", "Uhe가 방금 뭐라고 했어?"])
async def test_current_names_resolve_without_recent_messages_or_provider(runtime, text):
    rows, ids = await resolve(runtime, text)
    assert ids == (200,)
    assert rows[0]["names"] == ["tag : uhe", "Uhe", "2_718281"]
    assert rows[0]["reference"] in text
    runtime.resolver.assert_not_awaited()
    runtime.guild.fetch_member.assert_not_awaited()
    runtime.guild.chunk.assert_not_awaited()
    runtime.guild.fetch_members.assert_not_awaited()


async def test_current_directory_result_is_independent_of_recent_window(runtime):
    before = await resolve(runtime, "2_718281 핑해줘")
    for index in range(300):
        runtime.store.add_shared_call(Scope(1, 10, 500 + index, True), index + 1, "다른사람", "잡담")
        runtime.client.recent.add(Scope(1, 10, 500 + index), index + 1, "다른사람", "잡담")
    assert await resolve(runtime, "2_718281 핑해줘") == before
    runtime.guild.fetch_member.assert_not_awaited()


async def test_hidden_members_and_bots_cannot_be_identity_candidates(runtime):
    runtime.guild.members.extend([
        member(300), member(99, bot=True), member(400, bot=True),
    ])
    runtime.hidden_ids.add(300)
    rows, ids = await resolve(runtime, "2_718281 핑해줘")
    assert ids == (200,)
    assert rows[0]["user_id"] == "200"


async def test_current_speaker_can_be_called_by_their_own_username(runtime):
    caller = member(100, display_name="tag : tjrn", name="tjrn3712", global_name=None)
    runtime.message.author = caller
    runtime.guild.members.append(caller)
    rows, ids = await resolve(runtime, "tjrn3712 불러줘")
    assert ids == (100,)
    assert rows[0]["names"] == ["tag : tjrn", "tjrn3712"]
    runtime.resolver.assert_not_awaited()


async def test_duplicate_name_does_not_pick_first_cached_member(runtime):
    runtime.guild.members.append(member(300, name="other", global_name="Other"))
    assert await resolve(runtime, "tag : uhe 불러줘") == ([], ())
    runtime.resolver.assert_not_awaited()


async def test_partial_cache_does_not_establish_uniqueness(runtime):
    runtime.guild.chunked = False
    assert await resolve(runtime, "2_718281 핑해줘") == ([], ())
    runtime.resolver.assert_not_awaited()
    runtime.guild.fetch_member.assert_not_awaited()


async def test_large_directory_is_locally_narrowed_for_romanized_nickname(runtime):
    runtime.target.display_name = "tag : sendol"
    runtime.target.name = "sendol"
    runtime.target.global_name = "Sendol"
    runtime.guild.members.extend(
        member(1000 + i, display_name=f"user_{i}", name=f"user_{i}", global_name=None)
        for i in range(150)
    )
    rows, ids = await resolve(runtime, "센돌 불러줘")
    assert ids == (200,)
    assert rows[0]["reference"] == "센돌"
    supplied = runtime.resolver.await_args.args[1]
    assert len(supplied) == 1
    assert supplied[0]["user_id"] == "200"
    assert not any(name.startswith("user_") for row in supplied for name in row["names"])
    runtime.guild.fetch_member.assert_not_awaited()


async def test_no_name_evidence_does_not_export_any_members(runtime):
    assert await resolve(runtime, "없는이름 불러줘") == ([], ())
    runtime.resolver.assert_not_awaited()


async def test_oversized_fuzzy_shortlist_is_ambiguous_without_provider_call(runtime):
    runtime.guild.members = [
        member(i, display_name="duplicate", name=f"other_{i}", global_name=None)
        for i in range(200, 234)
    ]
    assert await resolve(runtime, "dupl1cate 불러줘") == ([], ())
    runtime.resolver.assert_not_awaited()


@pytest.mark.parametrize("gate", ["strict_egress", "third_party_mention"])
async def test_privacy_gates_apply_before_directory_matching(runtime, gate):
    runtime.store.identity_candidates = MagicMock(side_effect=AssertionError("must not read aliases"))
    assert await resolve(runtime, "2_718281 핑해줘", **{gate: True}) == ([], ())
    runtime.resolver.assert_not_awaited()
    runtime.store.identity_candidates.assert_not_called()


async def test_incomplete_cache_historical_fallback_revalidates_live_member(runtime):
    runtime.guild.members = []
    runtime.guild.chunked = False
    runtime.store.add_shared_call(Scope(1, 10, 200, True), 1, "tag : uhe", "발언")
    runtime.resolver.return_value = SpeakerIdentityResolution("resolved", "200", "tag : uhe")
    rows, ids = await resolve(runtime, "tag : uhe 불러줘")
    assert ids == (200,)
    assert "2_718281" in rows[0]["names"]
    runtime.guild.fetch_member.assert_awaited_once_with(200)
    runtime.guild.chunk.assert_not_awaited()
    runtime.guild.fetch_members.assert_not_awaited()


async def test_historical_fallback_rejects_departed_or_newly_hidden_member(runtime):
    runtime.guild.members = []
    runtime.guild.chunked = False
    runtime.store.add_shared_call(Scope(1, 10, 200, True), 1, "tag : uhe", "발언")
    runtime.hidden_ids.add(200)
    assert await resolve(runtime, "tag : uhe 불러줘") == ([], ())
    runtime.resolver.assert_not_awaited()
    runtime.hidden_ids.clear()
    runtime.guild.fetch_member.side_effect = discord.NotFound(NS(status=404, reason="missing"), "")
    assert await resolve(runtime, "tag : uhe 불러줘") == ([], ())
    runtime.resolver.assert_not_awaited()


async def test_current_aliases_have_reserved_space_before_old_names(runtime):
    runtime.target.display_name = "tag : sendol"
    runtime.target.global_name = "Sendol"
    runtime.target.name = "sendol"
    for i in range(4):
        runtime.store.add_shared_call(Scope(1, 10, 200, True), i + 1, f"old-{i}", "발언")
    rows, ids = await resolve(runtime, "센돌 불러줘")
    assert ids == (200,)
    assert rows[0]["names"][:3] == ["tag : sendol", "Sendol", "sendol"]
    assert len(rows[0]["names"]) == 4


async def test_alias_from_now_hidden_source_does_not_reach_semantic_resolver(runtime):
    hidden = MagicMock(spec=discord.TextChannel)
    hidden.permissions_for.return_value = NS(view_channel=False, read_message_history=False)
    runtime.guild.get_channel = lambda cid: runtime.channel if cid == 10 else hidden
    runtime.store.add_shared_call(Scope(1, 10, 200, True), 1, "visible-alias", "공개")
    runtime.store.add_shared_call(Scope(1, 11, 200, True), 2, "secret-alias", "예전 공개")
    runtime.resolver.return_value = SpeakerIdentityResolution("resolved", "200", "visible-alias")
    rows, ids = await resolve(runtime, "visible-alias가 누군지 알아?")
    assert ids == (200,)
    assert "secret-alias" not in str(runtime.resolver.await_args)
    assert "secret-alias" not in str(rows)
    runtime.resolver.reset_mock()
    assert await resolve(runtime, "secret-alias 불러줘") == ([], ())
    runtime.resolver.assert_not_awaited()


@pytest.mark.parametrize("text", ["2_718281 핑해줘", "tag : uhe 불러줘", "Uhe가 방금 뭐라고 했어?"])
def test_identity_and_recent_speech_do_not_authorize_public_memory(text):
    assert _public_context_request(Scope(1, 10, 100), text, [], resolved_user_ids=(200,)) == (
        False, (),
    )


@pytest.mark.parametrize("raises", [False, True])
async def test_adapter_identity_is_scoped_to_each_concurrent_turn_and_reset_on_failure(
    runtime, monkeypatch, raises,
):
    runtime.store.set_chat_log_mode_override("global", "off")
    monkeypatch.setattr(web_bot, "collect_reply_context", AsyncMock(return_value=[]))
    monkeypatch.setattr(web_bot, "collect_visual_inputs", AsyncMock(return_value=[]))
    runtime.guild.members.append(member(300, display_name="Other", name="other_name", global_name=None))
    arrived = asyncio.Event()
    captured = {}

    async def delegated(client, message):
        before = tuple(row["user_id"] for row in CURRENT_RESOLVED_IDENTITIES.get())
        captured[message.id] = before
        if len(captured) == 2:
            arrived.set()
        await asyncio.wait_for(arrived.wait(), timeout=1)
        assert tuple(row["user_id"] for row in CURRENT_RESOLVED_IDENTITIES.get()) == before
        if raises:
            raise RuntimeError("answer failed")

    monkeypatch.setattr(web_bot.BaseHinaClient, "on_message", delegated)

    async def turn(message_id, text):
        message = NS(
            id=message_id, guild=runtime.guild, channel=runtime.channel,
            author=NS(id=100, bot=False), content=text, mentions=[], webhook_id=None,
        )
        try:
            await runtime.client.on_message(message)
        finally:
            assert CURRENT_RESOLVED_IDENTITIES.get() == ()

    results = await asyncio.gather(
        turn(1, "히나야 2_718281 핑해줘"), turn(2, "히나야 other_name 불러줘"),
        return_exceptions=True,
    )
    assert captured == {1: ("200",), 2: ("300",)}
    assert all(isinstance(result, RuntimeError) for result in results) if raises else results == [None, None]
    assert CURRENT_RESOLVED_IDENTITIES.get() == ()

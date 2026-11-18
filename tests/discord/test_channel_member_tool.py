from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest

from hina_bot.ai.local_tools import LocalToolCall
from hina_bot.discord.member_directory import (
    CHANNEL_MEMBER_TOOL,
    channel_member_tool_registry,
    current_channel_members,
)


def member(
    user_id,
    *,
    nick="",
    global_name="",
    username="user",
    bot=False,
):
    return NS(
        id=user_id,
        nick=nick,
        global_name=global_name,
        name=username,
        bot=bot,
    )


def runtime(*, chunked=True):
    author = member(100, nick="caller", global_name="Caller", username="caller_id")
    target = member(200, nick="tag : uhe", global_name="Uhe", username="2_718281")
    hidden = member(300, nick="hidden", global_name="Hidden", username="hidden")
    other_bot = member(400, nick="bot", global_name="Bot", username="other_bot", bot=True)
    self_bot = member(99, nick="Hina", global_name="Hina", username="hina", bot=True)

    channel = MagicMock(spec=discord.TextChannel)
    channel.id = 10

    def permissions(value):
        return NS(view_channel=getattr(value, "id", None) != 300)

    channel.permissions_for.side_effect = permissions
    guild = NS(
        id=1,
        unavailable=False,
        chunked=chunked,
        members=[target, hidden, other_bot, self_bot],
        me=self_bot,
    )
    message = NS(guild=guild, channel=channel, author=author)
    return message


def test_channel_member_tool_description_carries_minimal_usage_guidance():
    description = CHANNEL_MEMBER_TOOL.description

    assert "이름·별명으로 사람을 식별" in description
    assert "정확한 mention ID" in description
    assert "반환된 user_id를 <@user_id>로 사용할 수" in description
    assert "여러 후보가 그럴듯하면 임의로 고르지" in description


def test_current_channel_members_returns_only_visible_humans_and_current_names():
    directory = current_channel_members(runtime(), 99)

    assert directory.complete is True
    assert directory.members == (
        {
            "user_id": "200",
            "server_nickname": "tag : uhe",
            "global_name": "Uhe",
            "username": "2_718281",
        },
        {
            "user_id": "100",
            "server_nickname": "caller",
            "global_name": "Caller",
            "username": "caller_id",
        },
    )


@pytest.mark.asyncio
async def test_channel_member_tool_returns_complete_directory():
    registry = channel_member_tool_registry(runtime(), 99)
    assert registry is not None

    result = await registry.execute(
        LocalToolCall("call-1", "get_current_channel_members", {})
    )

    assert result.is_error is False
    assert result.output["count"] == 2
    assert result.output["members"][0]["server_nickname"] == "tag : uhe"
    assert result.output["members"][0]["global_name"] == "Uhe"
    assert result.output["members"][0]["username"] == "2_718281"


@pytest.mark.asyncio
async def test_channel_member_tool_fails_closed_when_directory_is_incomplete():
    registry = channel_member_tool_registry(runtime(chunked=False), 99)
    assert registry is not None

    result = await registry.execute(
        LocalToolCall("call-1", "get_current_channel_members", {})
    )

    assert result.is_error is True
    assert result.output == {"error": "member_directory_incomplete"}


@pytest.mark.asyncio
async def test_thread_member_lookup_uses_thread_permissions():
    message = runtime()
    thread = MagicMock(spec=discord.Thread)
    thread.id = 10
    thread.type = discord.ChannelType.public_thread
    thread.permissions_for.side_effect = message.channel.permissions_for.side_effect
    message.channel = thread

    registry = channel_member_tool_registry(message, 99)
    assert registry is not None
    assert [tool["name"] for tool in registry.schemas()] == ["get_current_channel_members"]

    result = await registry.execute(LocalToolCall("thread", "get_current_channel_members", {}))
    assert result.is_error is False
    assert [row["user_id"] for row in result.output["members"]] == ["200", "100"]
    assert result.output["count"] == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["dm", "unsupported_guild_channel"])
async def test_channel_member_schema_is_offered_but_unsupported_scope_is_denied(kind):
    if kind == "dm":
        message = NS(guild=None, channel=MagicMock(spec=discord.DMChannel),
                     author=member(100))
    else:
        message = runtime()
        message.channel = MagicMock(spec=discord.VoiceChannel)

    registry = channel_member_tool_registry(message, 99)
    assert registry is not None
    assert [tool["name"] for tool in registry.schemas()] == ["get_current_channel_members"]
    result = await registry.execute(LocalToolCall("unsupported", "get_current_channel_members", {}))
    assert result.is_error is True
    assert result.output == {"error": "unsupported_scope"}


def private_thread_message(*, chunked=True, participant_ids=(100, 200, 99, 400)):
    message = runtime(chunked=chunked)
    members = {person.id: person for person in [message.author, *message.guild.members]}
    guild = message.guild
    guild.me = members[99]
    guild.get_member = members.get
    thread = MagicMock(spec=discord.Thread)
    thread.id = 10
    thread.type = discord.ChannelType.private_thread
    thread.member_count = len(participant_ids)
    thread.permissions_for.side_effect = lambda person: NS(
        view_channel=person.id != 300, manage_threads=False,
    )
    thread.fetch_members = AsyncMock(
        return_value=[NS(id=user_id) for user_id in participant_ids],
    )
    message.channel = thread
    return message


@pytest.mark.asyncio
async def test_private_channel_excludes_members_without_view_permission():
    message = runtime()
    # A role/permission-overwrite restricted text channel uses per-member permissions.
    result = await channel_member_tool_registry(message, 99).execute(
        LocalToolCall("private-text", "get_current_channel_members", {}),
    )
    assert result.is_error is False
    assert [row["user_id"] for row in result.output["members"]] == ["200", "100"]
    assert "300" not in str(result.output)


@pytest.mark.asyncio
async def test_private_thread_returns_only_verified_participants():
    message = private_thread_message()
    result = await channel_member_tool_registry(message, 99).execute(
        LocalToolCall("private", "get_current_channel_members", {}),
    )
    assert result.is_error is False
    assert [row["user_id"] for row in result.output["members"]] == ["100", "200"]
    assert result.output["count"] == 2
    message.channel.fetch_members.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize(("member_ids", "chunked", "expected_error"), [
    ((200, 99), True, "permission_denied"),
    ((100, 200), True, "permission_denied"),
    ((100, 200, 99), False, "member_directory_incomplete"),
    ((100, 200, 99, 999), True, "member_directory_incomplete"),
    (tuple(range(1, 101)), True, "member_directory_incomplete"),
])
async def test_private_thread_member_list_fails_closed_on_incomplete_or_missing_access(
    member_ids, chunked, expected_error,
):
    message = private_thread_message(chunked=chunked, participant_ids=member_ids)
    result = await channel_member_tool_registry(message, 99).execute(
        LocalToolCall("private", "get_current_channel_members", {}),
    )
    assert result.is_error is True
    assert result.output == {"error": expected_error}


@pytest.mark.asyncio
async def test_private_thread_member_fetch_error_has_no_partial_roster():
    message = private_thread_message()
    message.channel.fetch_members.side_effect = discord.HTTPException(
        MagicMock(status=503, reason="failed"), "unavailable",
    )
    result = await channel_member_tool_registry(message, 99).execute(
        LocalToolCall("failure", "get_current_channel_members", {}),
    )
    assert result.is_error is True
    assert result.output == {"error": "member_directory_incomplete"}



@pytest.mark.asyncio
async def test_private_text_channel_rejects_roster_after_bot_loses_visibility():
    message = runtime()
    message.channel.permissions_for.side_effect = lambda person: NS(
        view_channel=person.id != 99,
    )
    result = await channel_member_tool_registry(message, 99).execute(
        LocalToolCall("revoked", "get_current_channel_members", {}),
    )
    assert result.is_error is True
    assert result.output == {"error": "member_directory_incomplete"}


@pytest.mark.asyncio
async def test_natural_name_to_relationship_only_projects_authorized_axes():
    import json

    from hina_bot.ai.local_tools import LocalToolExecutor, LocalToolRegistry
    from hina_bot.core.routing import Scope
    from hina_bot.core.store import Store
    from hina_bot.discord.member_relationship import register_member_relationship

    message = runtime()
    guild = message.guild
    target = guild.members[0]
    for person in (target, message.author):
        person.display_name = person.nick
    guild.fetch_member = AsyncMock(side_effect=lambda uid: {200: target, 100: message.author}[uid])
    guild.fetch_channel = AsyncMock(return_value=message.channel)
    guild.default_role = object()
    message.channel.permissions_for.side_effect = lambda _: NS(view_channel=True, read_message_history=True)
    store = Store.create(":memory:")
    settings = NS(server_memory_read="on", personal_memory_cross_scope="off")
    client = NS(store=store, settings=settings, user=guild.me)
    registry = LocalToolRegistry()
    confirmed = set()
    channel_member_tool_registry(message, 99, registry=registry, confirmed_ids=confirmed)
    register_member_relationship(registry, client, message, Scope(1, 10, 100), confirmed)
    dm = Scope(None, 50, 200)
    store.record_completed_turn(dm, 123, "private source", "reply",
                                context_provenance={"source_trigger_kind": "explicit"})
    store.add_memory_item(dm, "private observation", kind="relationship", disclosure="implicit",
                          source_message_ids=["123"], relationship_evidence={"comfort": 3})
    call = LocalToolCall("lookup", "get_member_relationship", {"user_id": "200"})

    async def profile():
        result = await registry.execute(call)
        assert not result.is_error
        return result.output["profile"]

    assert await profile() == {}
    store.set_cross_scope_opt_in(100, True)
    rounds = []

    async def model(request):
        rounds.append(request)
        if len(rounds) == 1:
            name, arguments = "get_current_channel_members", {}
        elif len(rounds) == 2:
            directory = json.loads(request["input"][-1]["output"])["result"]
            person = next(row for row in directory["members"] if row["global_name"] == "Uhe")
            name, arguments = call.name, {"user_id": person["user_id"]}
        else:
            result = json.loads(request["input"][-1]["output"])
            assert result == {"ok": True, "result": {"user_id": "200", "profile": {}}}
            return NS(output=[], output_text="확실히 말하기 어려워.")
        return NS(output=[NS(type="function_call", call_id=str(len(rounds)), name=name,
                             arguments=json.dumps(arguments))])

    await LocalToolExecutor(registry).run(model, {"input": "너 Uhe랑 어떤 사이야?"})
    assert len(rounds) == 3
    store.set_cross_scope_opt_in(200, True)
    assert await profile() == {"comfort": 3}
    store.set_cross_scope_opt_in(200, False)
    settings.personal_memory_cross_scope = "on"
    assert await profile() == {"comfort": 3}
    store.set_user_memory_mode(200, "off")
    assert await profile() == {}
    store.set_user_memory_mode(200, "normal")
    store.forget_server_memory_source(None, 50, 123)
    assert await profile() == {}
    store.close()

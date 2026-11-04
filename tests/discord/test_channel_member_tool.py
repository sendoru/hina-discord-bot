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

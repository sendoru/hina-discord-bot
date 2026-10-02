from types import SimpleNamespace as NS
from unittest.mock import MagicMock

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


def test_channel_member_tool_is_not_exposed_outside_guild_text_channels():
    message = NS(guild=None, channel=NS(), author=member(100))
    assert channel_member_tool_registry(message, 99) is None

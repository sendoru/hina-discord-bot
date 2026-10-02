"""Request-scoped Discord member-list tool for conversational answers."""

from dataclasses import dataclass

import discord

from hina_bot.ai.local_tools import LocalToolError, LocalToolRegistry, LocalToolSpec


@dataclass(frozen=True)
class ChannelMemberDirectory:
    members: tuple[dict, ...]
    complete: bool


CHANNEL_MEMBER_TOOL = LocalToolSpec(
    name="get_current_channel_members",
    description=(
        "현재 Discord 채널에서 볼 수 있는 사람들의 user_id, 서버 닉네임, global name, "
        "username을 조회합니다. 사람을 이름·별명으로 식별하거나 정확한 사용자 mention ID가 "
        "필요할 때만 사용하세요."
    ),
    parameters={
        "type": "object",
        "properties": {},
        "additionalProperties": False,
    },
)


def _visible_human(message, member, bot_id: int) -> bool:
    if member is None or getattr(member, "bot", False):
        return False
    user_id = getattr(member, "id", None)
    if not isinstance(user_id, int) or user_id <= 0 or user_id == bot_id:
        return False
    channel = getattr(message, "channel", None)
    if not isinstance(channel, discord.TextChannel):
        return False
    return channel.permissions_for(member).view_channel is True


def _member_row(member) -> dict:
    return {
        "user_id": str(member.id),
        "server_nickname": str(getattr(member, "nick", "") or "")[:100],
        "global_name": str(getattr(member, "global_name", "") or "")[:100],
        "username": str(getattr(member, "name", "") or "")[:100],
    }


def current_channel_members(message, bot_id: int) -> ChannelMemberDirectory:
    guild = getattr(message, "guild", None)
    channel = getattr(message, "channel", None)
    if (
        guild is None
        or getattr(guild, "unavailable", False)
        or not isinstance(channel, discord.TextChannel)
        or channel.permissions_for(message.author).view_channel is not True
    ):
        return ChannelMemberDirectory((), False)

    rows = {}
    for member in getattr(guild, "members", ()):
        if _visible_human(message, member, bot_id):
            rows[str(member.id)] = _member_row(member)

    if _visible_human(message, message.author, bot_id):
        rows[str(message.author.id)] = _member_row(message.author)

    return ChannelMemberDirectory(
        tuple(rows.values()),
        getattr(guild, "chunked", False) is True,
    )


def channel_member_tool_registry(message, bot_id: int) -> LocalToolRegistry | None:
    if (
        getattr(message, "guild", None) is None
        or not isinstance(getattr(message, "channel", None), discord.TextChannel)
    ):
        return None

    registry = LocalToolRegistry()

    def get_current_channel_members(_arguments):
        directory = current_channel_members(message, bot_id)
        if not directory.complete:
            raise LocalToolError("member_directory_incomplete")
        return {
            "count": len(directory.members),
            "members": list(directory.members),
        }

    registry.register(CHANNEL_MEMBER_TOOL, get_current_channel_members)
    return registry


__all__ = [
    "CHANNEL_MEMBER_TOOL",
    "ChannelMemberDirectory",
    "channel_member_tool_registry",
    "current_channel_members",
]

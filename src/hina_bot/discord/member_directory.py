"""Request-scoped Discord member-list tool for conversational answers."""

from dataclasses import dataclass

import discord

from hina_bot.ai.local_tools import LocalToolError, LocalToolRegistry, LocalToolSpec

from .private_thread_access import check_private_thread_access, private_thread_member_ids


@dataclass(frozen=True)
class ChannelMemberDirectory:
    members: tuple[dict, ...]
    complete: bool


CHANNEL_MEMBER_TOOL = LocalToolSpec(
    name="get_current_channel_members",
    description=(
        "현재 Discord 서버 채널이나 스레드를 볼 수 있는 사용자 이름과 user_id를 조회합니다. "
        "비공개 스레드는 실제 참가자만 반환합니다. 이름·별명으로 사람을 "
        "식별하거나 정확한 mention ID가 필요할 때 사용하세요. 불러 달라거나 핑해 달라는 "
        "대상이 확인되면 반환된 user_id를 <@user_id>로 사용할 수 있으며, 여러 후보가 "
        "그럴듯하면 임의로 고르지 마세요."
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
    if not isinstance(channel, (discord.TextChannel, discord.Thread)):
        return False
    return channel.permissions_for(member).view_channel is True


def _member_row(member) -> dict:
    return {
        "user_id": str(member.id),
        "server_nickname": str(getattr(member, "nick", "") or "")[:100],
        "global_name": str(getattr(member, "global_name", "") or "")[:100],
        "username": str(getattr(member, "name", "") or "")[:100],
    }


def current_channel_members(
    message, bot_id: int, *, private_thread_ids: frozenset[int] | None = None,
) -> ChannelMemberDirectory:
    guild = getattr(message, "guild", None)
    channel = getattr(message, "channel", None)
    if (
        guild is None
        or getattr(guild, "unavailable", False)
        or not isinstance(channel, (discord.TextChannel, discord.Thread))
        or channel.permissions_for(message.author).view_channel is not True
    ):
        return ChannelMemberDirectory((), False)

    if isinstance(channel, discord.Thread) and channel.type == discord.ChannelType.private_thread:
        if private_thread_ids is None or not getattr(guild, "chunked", False):
            return ChannelMemberDirectory((), False)
        rows = []
        for user_id in sorted(private_thread_ids):
            member = guild.get_member(user_id)
            if member is None:
                return ChannelMemberDirectory((), False)
            if _visible_human(message, member, bot_id):
                rows.append(_member_row(member))
        return ChannelMemberDirectory(tuple(rows), True)

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


def channel_member_tool_registry(
    message, bot_id: int, *, registry: LocalToolRegistry | None = None,
) -> LocalToolRegistry | None:
    if registry is None:
        registry = LocalToolRegistry()

    async def get_current_channel_members(_arguments):
        channel = getattr(message, "channel", None)
        guild = getattr(message, "guild", None)
        if guild is None or not isinstance(channel, (discord.TextChannel, discord.Thread)):
            raise LocalToolError("unsupported_scope")
        private_ids = None
        if (isinstance(channel, discord.Thread)
                and channel.type == discord.ChannelType.private_thread):
            # Check current caller/bot permissions before fetching any private roster.
            for candidate in (guild.get_member(message.author.id), guild.me):
                if candidate is None or channel.permissions_for(candidate).view_channel is not True:
                    raise LocalToolError("permission_denied")
            private_ids = await private_thread_member_ids(channel)
            check_private_thread_access(channel, guild, message.author.id, bot_id, private_ids)
        directory = current_channel_members(message, bot_id, private_thread_ids=private_ids)
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

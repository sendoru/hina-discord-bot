"""Request-time checks for private Discord thread membership and visibility."""

import asyncio

import discord

from hina_bot.ai.local_tools import LocalToolError


async def private_thread_member_ids(thread: discord.Thread) -> frozenset[int]:
    """Fetch an authoritative member snapshot, never a partial gateway cache."""
    try:
        async with asyncio.timeout(5):
            members = await thread.fetch_members()
    except discord.Forbidden:
        raise LocalToolError("permission_denied") from None
    except (discord.HTTPException, TimeoutError):
        raise LocalToolError("member_directory_incomplete") from None

    if not isinstance(members, (list, tuple)):
        raise LocalToolError("member_directory_incomplete")
    ids = [getattr(member, "id", None) for member in members]
    if any(type(user_id) is not int or user_id <= 0 for user_id in ids):
        raise LocalToolError("member_directory_incomplete")
    if len(set(ids)) != len(ids):
        raise LocalToolError("member_directory_incomplete")

    # Discord's thread-members endpoint is limited to 100 items per page.
    # discord.py's fetch_members() has no pagination arguments: avoid claiming
    # an exhaustive directory (or using an incomplete access proof) at the limit.
    if len(ids) >= 100:
        raise LocalToolError("member_directory_incomplete")
    count = getattr(thread, "member_count", None)
    if type(count) is int and count > len(ids):
        raise LocalToolError("member_directory_incomplete")
    return frozenset(ids)


def check_private_thread_access(
    thread: discord.Thread, guild, author_id: int, bot_id: int,
    member_ids: frozenset[int],
) -> None:
    """The parent-channel permission is not proof of private-thread access."""
    if getattr(guild, "unavailable", False):
        raise LocalToolError("permission_denied")
    author = guild.get_member(author_id)
    bot = guild.me
    if author is None or bot is None or bot.id != bot_id:
        raise LocalToolError("permission_denied")
    for member in (author, bot):
        permissions = thread.permissions_for(member)
        if permissions.view_channel is not True:
            raise LocalToolError("permission_denied")
        if member.id not in member_ids and permissions.manage_threads is not True:
            raise LocalToolError("permission_denied")

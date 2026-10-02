"""Local current-member identities and visibility-scoped historical alias evidence."""

from dataclasses import dataclass

import discord


@dataclass(frozen=True)
class MemberIdentityDirectory:
    candidates: tuple[dict, ...]
    complete: bool


def member_names(member) -> list[str]:
    return list(dict.fromkeys(
        str(value).strip()[:100]
        for value in (
            getattr(member, "display_name", ""),
            getattr(member, "global_name", ""),
            getattr(member, "name", ""),
        )
        if value and str(value).strip()
    ))


def visible_member(message, member, bot_id: int) -> bool:
    if member is None or getattr(member, "bot", False):
        return False
    uid = getattr(member, "id", None)
    if not isinstance(uid, int) or uid <= 0 or uid == bot_id:
        return False
    channel = getattr(message, "channel", None)
    if not isinstance(channel, discord.TextChannel):
        # Private threads need a separate membership check, not just parent permissions.
        return False
    return channel.permissions_for(member).view_channel is True


def current_member_directory(message, bot_id: int, *, members_intent: bool):
    guild = message.guild
    channel = getattr(message, "channel", None)
    if (guild is None or getattr(guild, "unavailable", False)
            or not isinstance(channel, discord.TextChannel)
            or channel.permissions_for(message.author).view_channel is not True):
        return MemberIdentityDirectory((), False)
    rows = {}
    for member in getattr(guild, "members", ()):
        if not visible_member(message, member, bot_id):
            continue
        names = member_names(member)
        if names:
            rows[str(member.id)] = {"user_id": str(member.id), "names": names}
    # The current message may carry fresher caller metadata than the cached member object.
    if visible_member(message, message.author, bot_id):
        names = member_names(message.author)
        if names:
            rows[str(message.author.id)] = {"user_id": str(message.author.id), "names": names}
    return MemberIdentityDirectory(
        tuple(rows.values()),
        members_intent and getattr(guild, "chunked", False) is True,
    )


def historical_identity_candidates(message, bot_id: int, store) -> list[dict]:
    """Keep each historical alias within its own currently public, readable source."""
    guild = message.guild
    excluded = {message.author.id, bot_id}
    raw = store.identity_candidates(guild.id, exclude_user_ids=excluded)
    allowed_channels = set()
    for channel_id in {value for row in raw for value in row.get("channel_ids", ())}:
        channel = guild.get_channel(channel_id)
        if not isinstance(channel, discord.TextChannel):
            continue
        public = channel.permissions_for(guild.default_role)
        caller = channel.permissions_for(message.author)
        if (public.view_channel is True and public.read_message_history is True
                and caller.view_channel is True and caller.read_message_history is True):
            allowed_channels.add(channel_id)
    if not allowed_channels:
        return []
    rows = store.identity_candidates(
        guild.id, exclude_user_ids=excluded, allowed_channel_ids=allowed_channels,
    )
    # Current names have reserved space. Old aliases never crowd out the username.
    for row in rows:
        row["historical_names"] = tuple(row["names"])
        member = guild.get_member(int(row["user_id"]))
        current = member_names(member) if member is not None else []
        row["names"] = list(dict.fromkeys([*current, *row["names"]]))[:4]
    return rows


__all__ = [
    "MemberIdentityDirectory", "current_member_directory", "historical_identity_candidates",
    "member_names", "visible_member",
]

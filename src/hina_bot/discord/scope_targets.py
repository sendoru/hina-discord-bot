"""Resolve Discord-native channel selections without accepting arbitrary scope IDs."""

import discord

from hina_bot.core.routing import Scope


def command_target_scope(
    interaction: discord.Interaction,
    *,
    target: str = "channel",
    channel: discord.TextChannel | discord.Thread | None = None,
) -> Scope:
    """Return the invoking scope, or a permitted same-guild target channel scope.

    A selected channel can only narrow/redirect a channel-specific operation.
    Server/global operations must not silently use a channel picker.
    """
    if interaction.channel_id is None:
        raise ValueError("채널 안에서 실행해 주세요.")

    current = Scope(interaction.guild_id, interaction.channel_id, interaction.user.id)
    if channel is None:
        return current

    if target != "channel":
        raise ValueError("다른 채널 지정은 target:channel에서만 사용할 수 있어요.")
    if current.guild_id is None:
        raise ValueError("DM에서는 다른 서버 채널을 지정할 수 없어요.")
    if getattr(getattr(channel, "guild", None), "id", None) != current.guild_id:
        raise ValueError("현재 서버에 속한 채널만 지정할 수 있어요.")
    if getattr(channel, "id", 0) <= 0:
        raise ValueError("유효한 채널을 선택해 주세요.")
    if not channel.permissions_for(interaction.user).view_channel:
        raise ValueError("조회 권한이 없는 채널은 지정할 수 없어요.")

    return Scope(current.guild_id, channel.id, current.user_id)

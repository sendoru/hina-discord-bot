"""Ephemeral recent-channel-context controls for bot administrators."""
import logging

import discord
from discord import app_commands

from hina_bot.core.chatlog_modes import set_unified_chatlog_mode as _set_mode_override
from hina_bot.core.chatlog_modes import unified_chatlog_chain as _mode_chain
from hina_bot.core.routing import Scope
from hina_bot.core.scope_overrides import scope_target_key

from .scope_targets import command_target_scope

log = logging.getLogger("hina")
_TARGET_CHOICES = [
    app_commands.Choice(name="현재 채널", value="channel"),
    app_commands.Choice(name="현재 서버", value="server"),
    app_commands.Choice(name="전역", value="global"),
]
_VALUE_CHOICES = [
    app_commands.Choice(name="all — 같은 채널의 일반 대화까지 포함", value="all"),
    app_commands.Choice(name="direct — 히나에게 직접 말한 대화만", value="direct"),
    app_commands.Choice(name="off — 최근 채널 대화 문맥 사용 안 함", value="off"),
    app_commands.Choice(name="inherit — 상위 설정 따르기", value="inherit"),
]
_SOURCE_LABEL = {"channel": "채널", "server": "서버", "global": "전역", "default": "기본값"}


@app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
@app_commands.allowed_installs(guilds=True, users=True)
class ChatLogCommands(app_commands.Group):
    def __init__(self, client):
        super().__init__(name="chatlog", description="최근 채널 대화 문맥 관리 (봇 관리자 전용)")
        self.client = client

    async def interaction_check(self, interaction):
        if interaction.user.id not in self.client.emoji_admin_ids:
            await interaction.response.send_message("봇 소유자 또는 지정된 관리자만 사용할 수 있어요.", ephemeral=True)
            return False
        return True

    @staticmethod
    def scope(interaction):
        if interaction.channel_id is None:
            raise ValueError("채널 안에서 실행해 주세요.")
        return Scope(interaction.guild_id, interaction.channel_id, interaction.user.id)

    async def on_error(self, interaction, error):
        log.warning("Chatlog command failed (%s)", type(error).__name__)
        text = "최근 대화 문맥 설정을 처리하지 못했어요. /state show로 현재 상태를 확인해 주세요."
        if interaction.response.is_done():
            await interaction.followup.send(text, ephemeral=True)
        else:
            await interaction.response.send_message(text, ephemeral=True)

    def _status_text(self, scope):
        chain = _mode_chain(self.client.store, scope)
        global_text = chain["global"] or "all (기본값)"
        lines = [f"최종 적용: **{chain['effective']}** (출처: {_SOURCE_LABEL[chain['source']]})",
                 f"전역: `{global_text}`"]
        parent = chain["global"] or "all"
        if scope.guild_id is not None:
            server = chain["server"]
            lines.append(f"서버: `{'상속 → ' + parent if server is None else server}`")
            parent = server or parent
        channel = chain["channel"]
        lines.append(f"채널: `{'상속 → ' + parent if channel is None else channel}`")
        if scope.guild_id is None:
            lines.append("DM에서는 최근 채널 대화 문맥을 사용하지 않아요.")
        elif chain["effective"] == "all":
            lines.append("같은 채널의 일반 대화까지 최근 문맥으로 수집·사용해요.")
        elif chain["effective"] == "direct":
            lines.append("히나에게 직접 말을 건 대화 중심으로만 최근 문맥을 수집·사용해요.")
        else:
            lines.append("최근 채널 대화 문맥을 수집하거나 사용하지 않아요.")
        return "최근 채널 대화 문맥\n" + "\n".join(lines)

    @app_commands.command(name="mode", description="최근 대화 문맥 범위를 all/direct/off 또는 상속으로 설정")
    @app_commands.describe(
        value="all/direct/off 또는 상위 설정 상속",
        target="적용 범위. 기본은 현재 채널",
        channel="대상 서버 채널 (target:channel일 때만, 생략하면 현재 채널)",
    )
    @app_commands.choices(value=_VALUE_CHOICES, target=_TARGET_CHOICES)
    async def mode(
        self,
        interaction: discord.Interaction,
        value: str,
        target: str = "channel",
        channel: discord.TextChannel | discord.Thread | None = None,
    ):
        try:
            scope = command_target_scope(interaction, target=target, channel=channel)
            key = scope_target_key(scope, target)
            if value == "inherit" and target == "global":
                raise ValueError("전역 chatlog 설정은 상속할 수 없어요. all/direct/off 중 하나를 선택해 주세요.")
            if value not in {choice.value for choice in _VALUE_CHOICES}:
                raise ValueError("알 수 없는 chatlog 모드예요.")
        except ValueError as exc:
            await interaction.response.send_message(str(exc), ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        async with self.client.channel_lock(scope):
            _set_mode_override(self.client.store, key, None if value == "inherit" else value)
            if target == "global":
                self.client.recent.clear_all()
            elif target == "server":
                self.client.recent.forget(scope)
            else:
                self.client.recent.clear_channel(scope)
        changed = {"channel": "채널", "server": "서버", "global": "전역"}[target]
        if channel is not None:
            changed = f"<#{scope.channel_id}> 채널"
        state = "상위 설정을 따르도록 변경" if value == "inherit" else f"{value}으로 변경"
        await interaction.followup.send(
            f"{changed} 최근 대화 문맥 설정을 {state}했어요.\n\n{self._status_text(scope)}",
            ephemeral=True,
        )

    @app_commands.command(name="clear", description="현재 채널의 임시 최근 대화 문맥 비우기")
    async def clear(self, interaction: discord.Interaction):
        try:
            scope = self.scope(interaction)
        except ValueError as exc:
            await interaction.response.send_message(str(exc), ephemeral=True)
            return
        async with self.client.channel_lock(scope):
            self.client.recent.clear_channel(scope)
        await interaction.response.send_message("현재 채널의 임시 최근 대화 문맥을 비웠어요. 장기 기억은 그대로 유지돼요.", ephemeral=True)

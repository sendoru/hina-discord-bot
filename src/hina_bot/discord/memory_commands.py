"""Persistent-memory controls for users and bot administrators."""
import logging

import discord
from discord import app_commands

from hina_bot.core.routing import Scope
from hina_bot.core.scope_overrides import MemoryMode, scope_target_key

from .scope_targets import command_target_scope

log = logging.getLogger("hina")

_TARGET_CHOICES = [
    app_commands.Choice(name="현재 채널", value="channel"),
    app_commands.Choice(name="현재 서버", value="server"),
    app_commands.Choice(name="전역", value="global"),
]
_VALUE_CHOICES = [
    app_commands.Choice(name="normal — 읽기/쓰기", value="normal"),
    app_commands.Choice(name="read_only — 읽기만", value="read_only"),
    app_commands.Choice(name="write_only — 쓰기만", value="write_only"),
    app_commands.Choice(name="off — 읽기/쓰기 끄기", value="off"),
    app_commands.Choice(name="inherit — 상위 설정 따르기", value="inherit"),
]
_PURGE_TARGET_CHOICES = [
    app_commands.Choice(name="현재 채널의 모든 사용자 기억", value="channel"),
    app_commands.Choice(name="현재 서버의 모든 사용자 기억", value="server"),
]
_SOURCE_LABEL = {"channel": "채널", "server": "서버", "global": "전역", "default": "기본값"}


@app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
@app_commands.allowed_installs(guilds=True, users=True)
class MemoryCommands(app_commands.Group):
    def __init__(self, client):
        super().__init__(name="memory", description="장기 기억 관리")
        self.client = client

    async def interaction_check(self, interaction):
        if interaction.user.id not in self.client.emoji_admin_ids:
            await interaction.response.send_message(
                "봇 소유자 또는 지정된 관리자만 사용할 수 있어요.", ephemeral=True)
            return False
        return True

    @staticmethod
    def scope(interaction):
        if interaction.channel_id is None:
            raise ValueError("채널 안에서 실행해 주세요.")
        return Scope(interaction.guild_id, interaction.channel_id, interaction.user.id)

    async def on_error(self, interaction, error):
        log.warning("Memory command failed (%s)", type(error).__name__)
        text = "기억 설정을 처리하지 못했어요. /state show로 현재 상태를 확인해 주세요."
        if interaction.response.is_done():
            await interaction.followup.send(text, ephemeral=True)
        else:
            await interaction.response.send_message(text, ephemeral=True)

    @staticmethod
    def _chain_lines(chain: dict, default: str, *, include_server: bool) -> list[str]:
        global_text = chain["global"] or f"{default} (기본값)"
        lines = [
            f"최종 적용: **{chain['effective']}** (출처: {_SOURCE_LABEL[chain['source']]})",
            f"전역: `{global_text}`",
        ]
        parent = chain["global"] or default
        if include_server:
            server = chain["server"]
            lines.append(f"서버: `{'상속 → ' + parent if server is None else server}`")
            parent = server or parent
        channel = chain["channel"]
        lines.append(f"채널: `{'상속 → ' + parent if channel is None else channel}`")
        return lines

    def _status_text(self, scope: Scope) -> str:
        memory = self.client.store.memory_mode_chain(scope)
        lines = self._chain_lines(memory, "normal", include_server=scope.guild_id is not None)
        mode = MemoryMode(str(memory["effective"]))
        lines.append(
            f"장기 기억 읽기: {'켜짐' if mode.reads else '꺼짐'} / "
            f"새 장기 기억 저장: {'켜짐' if mode.writes else '꺼짐'}")
        return "장기 기억\n" + "\n".join(lines)

    @app_commands.command(name="mode", description="전역/서버/채널 장기 기억 설정 또는 상속 지정")
    @app_commands.describe(
        value="적용할 모드. inherit는 상위 범위 설정을 따릅니다",
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
                raise ValueError("전역 설정은 상속할 상위 범위가 없어요. normal 등 실제 모드를 선택해 주세요.")
            if value not in {choice.value for choice in _VALUE_CHOICES}:
                raise ValueError("알 수 없는 기억 모드예요.")
        except ValueError as exc:
            await interaction.response.send_message(str(exc), ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)
        async with self.client.channel_lock(scope):
            self.client.store.set_memory_mode_override(key, None if value == "inherit" else value)
        changed = {"channel": "채널", "server": "서버", "global": "전역"}[target]
        if channel is not None:
            changed = f"<#{scope.channel_id}> 채널"
        state = "상위 설정을 따르도록 변경" if value == "inherit" else f"{value}로 변경"
        await interaction.followup.send(
            f"{changed} 장기 기억 설정을 {state}했어요.\n\n{self._status_text(scope)}", ephemeral=True)

    @app_commands.command(name="purge", description="선택한 범위의 모든 사용자 장기 기억 삭제")
    @app_commands.describe(
        target="삭제 범위",
        confirm="실제 삭제를 확인하려면 true",
        channel="삭제할 서버 채널 (target:channel일 때만, 생략하면 현재 채널)",
    )
    @app_commands.choices(target=_PURGE_TARGET_CHOICES)
    async def purge(
        self,
        interaction: discord.Interaction,
        target: str = "channel",
        confirm: bool = False,
        channel: discord.TextChannel | discord.Thread | None = None,
    ):
        try:
            scope = command_target_scope(interaction, target=target, channel=channel)
            if target not in {choice.value for choice in _PURGE_TARGET_CHOICES}:
                raise ValueError("알 수 없는 삭제 범위예요.")
            if target == "server" and scope.guild_id is None:
                raise ValueError("DM에서는 서버 전체 기억을 삭제할 수 없어요.")
        except ValueError as exc:
            await interaction.response.send_message(str(exc), ephemeral=True)
            return
        if not confirm:
            label = (
                f"<#{scope.channel_id}> 채널" if channel is not None
                else "현재 서버" if target == "server"
                else "현재 채널"
            )
            await interaction.response.send_message(
                f"삭제하지 않았어요. 대상: {label}. "
                "모든 사용자의 자동 기억을 삭제하려면 confirm을 true로 선택해 주세요.",
                ephemeral=True,
            )
            return

        await interaction.response.defer(ephemeral=True)
        async with self.client.channel_lock(scope):
            if target == "channel":
                deleted = self.client.store.purge_channel_memory(scope)
                label = f"<#{scope.channel_id}> 채널" if channel is not None else "현재 채널"
            else:
                # Only channel/server targets are admitted above. Global purge is Dashboard-only.
                deleted = self.client.store.purge_realm_memory(scope)
                label = "현재 서버"
        await interaction.followup.send(
            f"{label}의 사용자 장기 기억을 초기화했어요. 삭제된 저장 항목: {deleted}개. "
            "서버 공통 메모, 기억 모드 설정, 최근 채널 로그는 유지돼요.",
            ephemeral=True,
        )
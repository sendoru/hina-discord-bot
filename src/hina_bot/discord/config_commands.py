"""Bot-admin slash commands for hot-reloadable runtime settings."""

import logging

import discord
from discord import app_commands

from hina_bot.core.runtime_config import (
    RuntimeSettings,
)

from .scope_targets import command_target_scope

log = logging.getLogger("hina")

_PRIVACY_CHOICES = [
    app_commands.Choice(
        name="bot_interactions_only — 현재 채널의 히나 참여 대화만 외부 전송",
        value="bot_interactions_only",
    ),
    app_commands.Choice(
        name="full — 허용된 전체 문맥을 외부 모델에 제공",
        value="full",
    ),
    app_commands.Choice(
        name="startup — DB override를 지우고 .env/코드 기본값 사용",
        value="startup",
    ),
]



def apply_runtime_setting_side_effects(client, key: str) -> None:
    if key == "channel_context_chars":
        client.recent.budget = client.settings.channel_context_chars
    elif key in {"external_context_policy", "always_reply_channel_ids"}:
        # Direct-trigger provenance changes when either the privacy policy or implicit guild
        # trigger set changes. Do not let rows classified under the old policy survive.
        client.recent.clear_all()



class AlwaysReplyCommands(app_commands.Group):
    """Channel-scoped commands for the ALWAYS_REPLY_CHANNEL_IDS runtime set."""

    def __init__(self, client):
        super().__init__(
            name="always-reply",
            description="현재 서버의 채널별 자동 응답 관리 (봇 관리자 전용)",
        )
        self.client = client

    @staticmethod
    def _scope(interaction: discord.Interaction, channel):
        scope = command_target_scope(interaction, channel=channel)
        if scope.guild_id is None:
            raise ValueError("DM에서는 서버 채널의 always-reply 설정을 사용할 수 없어요.")
        invoking_channel = getattr(interaction, "channel", None)
        if (
            channel is None and invoking_channel is not None
            and not isinstance(invoking_channel, (discord.TextChannel, discord.Thread))
        ):
            raise ValueError("텍스트 채널이나 스레드에서 실행해 주세요.")
        return scope

    async def _mutate(self, interaction: discord.Interaction, channel, *, enabled: bool):
        try:
            scope = self._scope(interaction, channel)
            changed = self.client.settings.update_discord_id(
                "always_reply_channel_ids", scope.channel_id, enabled=enabled
            )
            if changed:
                apply_runtime_setting_side_effects(self.client, "always_reply_channel_ids")
        except ValueError as exc:
            await interaction.response.send_message(str(exc), ephemeral=True)
            return

        state = "켜짐" if enabled else "꺼짐"
        result = "변경했어요." if changed else "이미 해당 상태라 변경하지 않았어요."
        await interaction.response.send_message(
            f"<#{scope.channel_id}> 채널의 자동 응답: **{state}** — {result} "
            f"[설정 출처: {self.client.settings.source('always_reply_channel_ids')}]"
            + (" 기존 recent buffer도 초기화했어요." if changed else ""),
            ephemeral=True,
        )

    @app_commands.command(name="enable", description="현재/선택 채널의 자동 응답 켜기")
    @app_commands.describe(channel="다른 서버 채널 선택 (생략하면 현재 채널)")
    async def enable(
        self,
        interaction: discord.Interaction,
        channel: discord.TextChannel | discord.Thread | None = None,
    ):
        await self._mutate(interaction, channel, enabled=True)

    @app_commands.command(name="disable", description="현재/선택 채널의 자동 응답 끄기")
    @app_commands.describe(channel="다른 서버 채널 선택 (생략하면 현재 채널)")
    async def disable(
        self,
        interaction: discord.Interaction,
        channel: discord.TextChannel | discord.Thread | None = None,
    ):
        await self._mutate(interaction, channel, enabled=False)

    @app_commands.command(name="privacy", description="외부 LLM에 보낼 대화 문맥의 프라이버시 경계 설정")
    @app_commands.describe(value="외부 모델 문맥 정책 또는 startup 기본값으로 복귀")
    @app_commands.choices(value=_PRIVACY_CHOICES)
    async def privacy(self, interaction: discord.Interaction, value: str):
        try:
            if value == "startup":
                parsed = self.client.settings.reset("external_context_policy")
                source = "startup"
            elif value in {"full", "bot_interactions_only"}:
                parsed = self.client.settings.set_text("external_context_policy", value)
                source = "DB override"
            else:
                raise ValueError("알 수 없는 외부 문맥 정책이에요.")
            self._apply_side_effects("external_context_policy")
        except ValueError as exc:
            await interaction.response.send_message(str(exc), ephemeral=True)
            return

        if parsed == "bot_interactions_only":
            detail = (
                "현재 채널에서 사용자들이 히나를 직접 호출한 말과 히나 답변, "
                "호출자 본인의 허용된 기억만 외부 모델 요청에 포함해요. 대상 사용자 조회도 "
                "히나를 직접 호출한 발언만 허용하고, 일반 채널 잡담·cross-user memory·"
                "서버 공통 메모는 제외돼요."
            )
        else:
            detail = (
                "라우팅에서 허용된 주변 채팅·대상 사용자 문맥·공개 기억 등을 외부 모델에 "
                "제공할 수 있어요. 신뢰하는 provider에서만 사용해 주세요."
            )
        await interaction.response.send_message(
            f"외부 모델 문맥 정책을 `{parsed}`로 적용했어요. [{source}]\n{detail}\n"
            "정책 변경으로 기존 recent buffer도 초기화했어요.",
            ephemeral=True,
        )


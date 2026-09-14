"""Bot-admin slash commands for hot-reloadable runtime settings."""

import logging

import discord
from discord import app_commands

from hina_bot.core.runtime_config import (
    RUNTIME_SETTING_SPECS,
    RuntimeSettings,
    format_runtime_value,
)

log = logging.getLogger("hina")

_KEY_CHOICES = [
    app_commands.Choice(name=spec.env_name, value=attr)
    for attr, spec in RUNTIME_SETTING_SPECS.items()
    if attr != "external_context_policy"
]
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


@app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
@app_commands.allowed_installs(guilds=True, users=True)
class ConfigCommands(app_commands.Group):
    def __init__(self, client):
        super().__init__(name="config", description="런타임 설정 관리 (봇 관리자 전용)")
        self.client = client

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id not in self.client.emoji_admin_ids:
            await interaction.response.send_message(
                "봇 소유자 또는 지정된 관리자만 사용할 수 있어요.", ephemeral=True
            )
            return False
        if not isinstance(self.client.settings, RuntimeSettings):
            await interaction.response.send_message(
                "이 실행 방식에서는 런타임 설정 변경을 사용할 수 없어요.", ephemeral=True
            )
            return False
        return True

    async def on_error(self, interaction: discord.Interaction, error):
        log.warning("Config command failed (%s)", type(error).__name__)
        text = "런타임 설정을 처리하지 못했어요. /config status로 현재 상태를 확인해 주세요."
        if interaction.response.is_done():
            await interaction.followup.send(text, ephemeral=True)
        else:
            await interaction.response.send_message(text, ephemeral=True)

    def _apply_side_effects(self, key: str) -> None:
        if key == "channel_context_chars":
            self.client.recent.budget = self.client.settings.channel_context_chars
        elif key == "external_context_policy":
            # Do not let context collected under a broader policy survive a hot privacy change.
            # The final egress filter is still authoritative even if a future caller forgets this.
            self.client.recent.clear_all()

    @app_commands.command(name="status", description="현재 런타임 설정과 DB override 확인")
    async def status(self, interaction: discord.Interaction):
        lines = [
            "런타임 설정",
            "`DB`가 있으면 즉시 적용되고, 없으면 시작 시 읽은 .env/.env.local 또는 코드 기본값을 사용해요.",
            "",
        ]
        for spec, value, source in self.client.settings.rows():
            label = "DB" if source == "db" else "startup"
            lines.append(f"`{spec.env_name}` = `{format_runtime_value(value)}`  [{label}]")
        await interaction.response.send_message("\n".join(lines), ephemeral=True)

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
                "호출자 본인의 허용된 기억만 외부 모델 요청에 포함해요. 일반 채널 잡담·"
                "target history·cross-user memory·서버 공통 메모는 제외돼요."
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

    @app_commands.command(name="set", description="런타임 설정을 DB에 저장하고 즉시 적용")
    @app_commands.describe(
        key="변경할 설정",
        value="새 값. bool은 on/off, CALL_PREFIXES는 쉼표 구분, 위치를 비우려면 none",
    )
    @app_commands.choices(key=_KEY_CHOICES)
    async def set_config(self, interaction: discord.Interaction, key: str, value: str):
        if key == "external_context_policy":
            await interaction.response.send_message(
                "외부 모델 문맥 정책은 `/config privacy`에서 변경해 주세요.", ephemeral=True
            )
            return
        try:
            parsed = self.client.settings.set_text(key, value)
            self._apply_side_effects(key)
        except ValueError as exc:
            await interaction.response.send_message(str(exc), ephemeral=True)
            return
        spec = RUNTIME_SETTING_SPECS[key]
        await interaction.response.send_message(
            f"`{spec.env_name}`을 `{format_runtime_value(parsed)}`로 변경했어요. "
            "DB override라 재시작 후에도 유지돼요.",
            ephemeral=True,
        )

    @app_commands.command(name="reset", description="DB override를 삭제하고 시작 시 기본값으로 복귀")
    @app_commands.describe(key="초기화할 설정")
    @app_commands.choices(key=_KEY_CHOICES)
    async def reset(self, interaction: discord.Interaction, key: str):
        if key == "external_context_policy":
            await interaction.response.send_message(
                "외부 모델 문맥 정책은 `/config privacy value:startup`으로 초기화해 주세요.",
                ephemeral=True,
            )
            return
        value = self.client.settings.reset(key)
        self._apply_side_effects(key)
        spec = RUNTIME_SETTING_SPECS[key]
        await interaction.response.send_message(
            f"`{spec.env_name}`의 DB override를 삭제했어요. "
            f"이제 시작 시 값 `{format_runtime_value(value)}`을 사용해요.",
            ephemeral=True,
        )

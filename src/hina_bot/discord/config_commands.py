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

    @app_commands.command(name="set", description="런타임 설정을 DB에 저장하고 즉시 적용")
    @app_commands.describe(
        key="변경할 설정",
        value=(
            "새 값. bool은 on/off, CALL_PREFIXES는 쉼표 구분. "
            "RUNTIME_DEFAULT_LOCATION을 비우려면 none"
        ),
    )
    @app_commands.choices(key=_KEY_CHOICES)
    async def set_config(self, interaction: discord.Interaction, key: str, value: str):
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
        value = self.client.settings.reset(key)
        self._apply_side_effects(key)
        spec = RUNTIME_SETTING_SPECS[key]
        await interaction.response.send_message(
            f"`{spec.env_name}`의 DB override를 삭제했어요. "
            f"이제 시작 시 값 `{format_runtime_value(value)}`을 사용해요.",
            ephemeral=True,
        )

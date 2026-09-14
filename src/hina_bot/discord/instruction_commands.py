import logging

import discord
from discord import app_commands

from .admin_export import text_attachment
from .admin_list import created_compact, sort_rows
from .instructions import InstructionRegistry

log = logging.getLogger("hina")

_SORT_CHOICES = [
    app_commands.Choice(name="추가 시간순", value="time"),
    app_commands.Choice(name="최신 추가순", value="recent"),
    app_commands.Choice(name="ID순", value="id"),
    app_commands.Choice(name="상태순 (ON 먼저)", value="state"),
]
_SORT_LABELS = {choice.value: choice.name for choice in _SORT_CHOICES}


@app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
@app_commands.allowed_installs(guilds=True, users=True)
class InstructionCommands(app_commands.Group):
    def __init__(self, client):
        super().__init__(name="instruction", description="동적 캐릭터 instruction 관리 (봇 관리자 전용)")
        self.client = client
        self.registry = getattr(client.llm, "instructions", InstructionRegistry(None))

    async def interaction_check(self, interaction):
        # Bot ownership/BOT_ADMIN_IDS is the authority here, not guild Administrator permission.
        # This lets designated bot admins tune prompts from any server or DM where the app command
        # is available.
        if interaction.user.id not in self.client.emoji_admin_ids:
            await interaction.response.send_message(
                "봇 소유자 또는 지정된 관리자만 사용할 수 있어요.", ephemeral=True)
            return False
        return True

    async def on_error(self, interaction, error):
        log.warning("Instruction command failed (%s)", type(error).__name__)
        text = "instruction을 처리하지 못했어요. /instruction list로 현재 상태를 확인해 주세요."
        if interaction.response.is_done():
            await interaction.followup.send(text, ephemeral=True)
        else:
            await interaction.response.send_message(text, ephemeral=True)

    @app_commands.command(name="add", description="새 동적 instruction 추가 및 즉시 활성화")
    @app_commands.describe(identifier="영문 ID", text="히나에게 추가할 보조 지침")
    async def add(self, interaction: discord.Interaction, identifier: str, text: str):
        try:
            self.registry.add(identifier, text)
        except ValueError as exc:
            await interaction.response.send_message(str(exc), ephemeral=True)
            return
        await interaction.response.send_message(
            f"instruction `{identifier}`를 추가하고 활성화했어요.", ephemeral=True)

    @app_commands.command(name="list", description="instruction 검색·정렬 결과를 전체 내용 파일로 받기")
    @app_commands.describe(
        search="ID나 본문에서 찾을 검색어. 비워 두면 전체 표시",
        sort="목록 정렬 방식. 기본은 추가 시간순",
    )
    @app_commands.choices(sort=_SORT_CHOICES)
    async def list_items(
        self,
        interaction: discord.Interaction,
        search: str | None = None,
        sort: str = "time",
    ):
        rows = self.registry.list()
        total = len(rows)
        query = (search or "").strip().casefold()
        if query:
            rows = [row for row in rows if query in str(row.get("id", "")).casefold()
                    or query in str(row.get("text", "")).casefold()]
        if not rows:
            text = (f"`{discord.utils.escape_markdown(search.strip())}` 검색 결과가 없어요."
                    if search and search.strip() else "등록된 동적 instruction이 없어요.")
            await interaction.response.send_message(text, ephemeral=True)
            return

        rows = sort_rows(rows, sort, lambda row: row)
        if query:
            header = (f"동적 instruction 검색 결과 {len(rows)}/{total} · "
                      f"{_SORT_LABELS.get(sort, '추가 시간순')}")
        else:
            header = f"동적 instruction {len(rows)}/50 · {_SORT_LABELS.get(sort, '추가 시간순')}"

        lines = [header, ""]
        for row in rows:
            state = "ON" if row.get("enabled", True) else "OFF"
            lines.extend([
                f"[{row.get('id', '?')}] {state} · 추가(UTC) {created_compact(row)}",
                str(row.get("text", "")),
                "",
            ])
        filename = "instructions-search.txt" if query else "instructions.txt"
        await interaction.response.send_message(
            f"{header}\n전체 내용은 첨부 파일에 넣었어요.",
            file=text_attachment("\n".join(lines).rstrip() + "\n", filename),
            ephemeral=True,
        )

    @app_commands.command(name="edit", description="기존 instruction 본문 수정")
    @app_commands.describe(identifier="수정할 ID", text="새 보조 지침")
    async def edit(self, interaction: discord.Interaction, identifier: str, text: str):
        try:
            self.registry.edit(identifier, text)
        except ValueError as exc:
            await interaction.response.send_message(str(exc), ephemeral=True)
            return
        await interaction.response.send_message(
            f"instruction `{identifier}` 내용을 수정했어요.", ephemeral=True)

    @app_commands.command(name="enable", description="instruction 활성화")
    async def enable(self, interaction: discord.Interaction, identifier: str):
        await self._set_enabled(interaction, identifier, True)

    @app_commands.command(name="disable", description="instruction 비활성화")
    async def disable(self, interaction: discord.Interaction, identifier: str):
        await self._set_enabled(interaction, identifier, False)

    async def _set_enabled(self, interaction, identifier: str, enabled: bool):
        try:
            self.registry.set_enabled(identifier, enabled)
        except ValueError as exc:
            await interaction.response.send_message(str(exc), ephemeral=True)
            return
        state = "활성화" if enabled else "비활성화"
        await interaction.response.send_message(
            f"instruction `{identifier}`를 {state}했어요.", ephemeral=True)

    @app_commands.command(name="remove", description="instruction 영구 삭제")
    async def remove(self, interaction: discord.Interaction, identifier: str):
        try:
            self.registry.remove(identifier)
        except ValueError as exc:
            await interaction.response.send_message(str(exc), ephemeral=True)
            return
        await interaction.response.send_message(
            f"instruction `{identifier}`를 삭제했어요.", ephemeral=True)

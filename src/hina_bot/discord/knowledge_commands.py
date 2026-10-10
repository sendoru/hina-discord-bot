import logging

import discord
from discord import app_commands

from hina_bot.core.knowledge_ingest import KnowledgeIngestor

log = logging.getLogger("hina")

@app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
@app_commands.allowed_installs(guilds=True, users=True)
class KnowledgeCommands(app_commands.Group):
    def __init__(self, client):
        super().__init__(
            name="knowledge",
            description="조사 메모를 자동 분해·조정해 설정 사실·해석으로 반영 (봇 관리자 전용)",
        )
        self.client = client
        self.ingestor = KnowledgeIngestor(client.llm)

    async def interaction_check(self, interaction):
        if interaction.user.id not in self.client.emoji_admin_ids:
            await interaction.response.send_message(
                "봇 소유자 또는 지정된 관리자만 사용할 수 있어요.", ephemeral=True)
            return False
        return True

    async def on_error(self, interaction, error):
        log.warning("Knowledge command failed (%s)", type(error).__name__)
        text = "knowledge를 처리하지 못했어요. 잠시 후 다시 시도해 주세요."
        if interaction.response.is_done():
            await interaction.followup.send(text, ephemeral=True)
        else:
            await interaction.response.send_message(text, ephemeral=True)

    @app_commands.command(
        name="ingest",
        description="긴 조사 메모를 기존 knowledge와 조정해 자동 반영",
    )
    @app_commands.describe(text="정리할 최신 조사 메모. 사실과 추측이 섞여 있어도 됩니다 (최대 6000자)")
    async def ingest(self, interaction: discord.Interaction, text: str):
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            result = await self.ingestor.ingest(text)
        except (TypeError, ValueError) as exc:
            await interaction.followup.send(str(exc), ephemeral=True)
            return

        added = result["added"]
        updated = result["updated"]
        removed = result["removed"]
        held = result["held"]
        skipped = result["skipped"]
        lines = [
            (
                f"knowledge 반영 완료: 추가 {len(added)}개 / 갱신 {len(updated)}개 / "
                f"대체 삭제 {len(removed)}개"
            ),
            f"기존 내용 유지·중복: {len(skipped)}개 / 애매해서 보류: {len(held)}개",
        ]
        changed = added + updated
        if changed:
            lines.append("\n반영된 항목:")
            for item in changed[:12]:
                kind = "사실" if item["kind"] == "world_fact" else "해석"
                verb = "갱신" if item in updated else "추가"
                lines.append(f"- `{item['id']}` [{kind}/{verb}]")
            if len(changed) > 12:
                lines.append(f"- … 외 {len(changed) - 12}개")
        if removed:
            lines.append("\n중복·구버전으로 제거:")
            for item in removed[:6]:
                lines.append(f"- `{item['id']}` → `{item['superseded_by']}`")
            if len(removed) > 6:
                lines.append(f"- … 외 {len(removed) - 6}개")
        if held:
            lines.append("\n보류된 항목:")
            for item in held[:6]:
                reason = discord.utils.escape_markdown(item["reason"].replace("\n", " "))
                lines.append(f"- `{item['id']}` — {reason[:140]}")
            if len(held) > 6:
                lines.append(f"- … 외 {len(held) - 6}개")
        await interaction.followup.send("\n".join(lines)[:1900], ephemeral=True)


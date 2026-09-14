import logging

import discord
from discord import app_commands

from .admin_export import export_timestamp, text_attachment
from .admin_list import sort_rows
from .knowledge_ingest import KnowledgeIngestor
from .runtime_knowledge import RuntimeKnowledgeRegistry

log = logging.getLogger("hina")

_SORT_CHOICES = [
    app_commands.Choice(name="추가 시간순", value="time"),
    app_commands.Choice(name="최신 추가순", value="recent"),
    app_commands.Choice(name="ID순", value="id"),
    app_commands.Choice(name="상태순 (ON 먼저)", value="state"),
    app_commands.Choice(name="종류순 (사실→해석)", value="kind"),
]
_SORT_LABELS = {choice.value: choice.name for choice in _SORT_CHOICES}


@app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
@app_commands.allowed_installs(guilds=True, users=True)
class KnowledgeCommands(app_commands.Group):
    def __init__(self, client):
        super().__init__(
            name="knowledge",
            description="조사 메모를 자동 분해·조정해 설정 사실·해석으로 반영 (봇 관리자 전용)",
        )
        self.client = client
        self.fact_registry = getattr(
            client.llm, "runtime_lore", RuntimeKnowledgeRegistry(None, kind="world_fact"))
        self.context_registry = getattr(
            client.llm, "story_context", RuntimeKnowledgeRegistry(None, kind="interpretation"))
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

    def _all_rows(self):
        rows = []
        rows.extend(("fact", row) for row in self.fact_registry.list())
        rows.extend(("context", row) for row in self.context_registry.list())
        return rows

    def _find(self, identifier: str):
        matches = []
        for label, registry in (("fact", self.fact_registry), ("context", self.context_registry)):
            try:
                matches.append((label, registry, registry.get(identifier)))
            except ValueError:
                pass
        if not matches:
            raise ValueError("등록되지 않은 knowledge ID입니다.")
        if len(matches) > 1:
            raise ValueError("같은 ID가 사실/해석 양쪽에 있어 자동 처리할 수 없습니다.")
        return matches[0]

    @staticmethod
    def _export_entry(label: str, row: dict) -> str:
        state = "ON" if row["enabled"] else "OFF"
        kind = "사실" if label == "fact" else "해석"
        return "\n".join([
            f"[{row['id']}] {kind}/{state}",
            f"created_at: {export_timestamp(row.get('created_at'))}",
            f"updated_at: {export_timestamp(row.get('updated_at'))}",
            f"awareness: {row['awareness']}",
            f"timeline: {row['timeline']}",
            f"subjects: {', '.join(row['subjects'])}",
            f"keywords: {', '.join(row['keywords'])}",
            "",
            str(row["content"]),
        ])

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

    @app_commands.command(name="list", description="knowledge 검색·정렬 결과를 전체 내용 파일로 받기")
    @app_commands.describe(
        search="ID·본문·키워드·대상·시점에서 찾을 검색어. 비워 두면 전체 표시",
        sort="목록 정렬 방식. 기본은 추가 시간순",
    )
    @app_commands.choices(sort=_SORT_CHOICES)
    async def list_items(
        self,
        interaction: discord.Interaction,
        search: str | None = None,
        sort: str = "time",
    ):
        try:
            rows = self._all_rows()
        except (TypeError, ValueError) as exc:
            await interaction.response.send_message(str(exc), ephemeral=True)
            return
        total = len(rows)
        query = (search or "").strip().casefold()
        if query:
            filtered = []
            for label, row in rows:
                haystack = "\n".join([
                    str(row.get("id", "")),
                    str(row.get("content", "")),
                    " ".join(row.get("keywords", [])),
                    " ".join(row.get("subjects", [])),
                    str(row.get("timeline", "")),
                    "사실 world_fact" if label == "fact" else "해석 interpretation",
                ]).casefold()
                if query in haystack:
                    filtered.append((label, row))
            rows = filtered
        if not rows:
            text = (f"`{discord.utils.escape_markdown(search.strip())}` 검색 결과가 없어요."
                    if search and search.strip() else "자동 반영된 knowledge가 없어요.")
            await interaction.response.send_message(text, ephemeral=True)
            return

        if sort == "kind":
            rows = sorted(rows, key=lambda item: (item[0] != "fact", item[1]["id"].casefold()))
        else:
            rows = sort_rows(rows, sort, lambda item: item[1])
        if query:
            header = f"knowledge 검색 결과 {len(rows)}/{total} · {_SORT_LABELS.get(sort, '추가 시간순')}"
        else:
            header = f"knowledge {len(rows)}/200 · {_SORT_LABELS.get(sort, '추가 시간순')}"

        sections = [header]
        sections.extend(self._export_entry(label, row) for label, row in rows)
        filename = "knowledge-search.txt" if query else "knowledge.txt"
        await interaction.response.send_message(
            f"{header}\n전체 내용과 메타데이터는 첨부 파일에 넣었어요.",
            file=text_attachment("\n\n---\n\n".join(sections) + "\n", filename),
            ephemeral=True,
        )

    @app_commands.command(name="show", description="knowledge 한 항목을 전체 내용 파일로 받기")
    async def show(self, interaction: discord.Interaction, identifier: str):
        try:
            label, _, row = self._find(identifier)
        except ValueError as exc:
            await interaction.response.send_message(str(exc), ephemeral=True)
            return
        kind = "사실" if label == "fact" else "해석"
        state = "ON" if row["enabled"] else "OFF"
        await interaction.response.send_message(
            f"`{row['id']}` [{kind}/{state}] 전체 내용은 첨부 파일에 넣었어요.",
            file=text_attachment(
                self._export_entry(label, row) + "\n",
                f"knowledge-{row['id']}.txt",
            ),
            ephemeral=True,
        )

    async def _set_enabled(self, interaction, identifier: str, enabled: bool):
        try:
            _, registry, _ = self._find(identifier)
            registry.set_enabled(identifier, enabled)
        except ValueError as exc:
            await interaction.response.send_message(str(exc), ephemeral=True)
            return
        state = "활성화" if enabled else "비활성화"
        await interaction.response.send_message(
            f"knowledge `{identifier}`를 {state}했어요.", ephemeral=True)

    @app_commands.command(name="enable", description="knowledge 항목 활성화")
    async def enable(self, interaction: discord.Interaction, identifier: str):
        await self._set_enabled(interaction, identifier, True)

    @app_commands.command(name="disable", description="knowledge 항목 비활성화")
    async def disable(self, interaction: discord.Interaction, identifier: str):
        await self._set_enabled(interaction, identifier, False)

    @app_commands.command(name="remove", description="knowledge 항목 영구 삭제")
    async def remove(self, interaction: discord.Interaction, identifier: str):
        try:
            _, registry, _ = self._find(identifier)
            registry.remove(identifier)
        except ValueError as exc:
            await interaction.response.send_message(str(exc), ephemeral=True)
            return
        await interaction.response.send_message(
            f"knowledge `{identifier}`를 삭제했어요.", ephemeral=True)

"""Discord slash-command surface for runtime user/admin controls."""

import logging
import re

import discord
from discord import app_commands

from hina_bot.core.routing import Scope

from .chatlog_commands import ChatLogCommands
from .note_commands import NoteCommands

log = logging.getLogger("hina")

_ADMIN_MEMORY_COMMANDS = {"mode", "status", "overview", "purge"}
_EMOJI_ALIAS_RE = re.compile(r"[a-z][a-z0-9_]{1,31}")

HELP_TEXT = """히나와 DM으로 대화하려면 이 도움말 메시지의 히나 프로필을 눌러 `메시지 보내기`를 선택해 주세요.
DM에서는 메시지 맨 앞에 `히나야`를 붙여 말을 걸 수 있습니다.

서버에서는 @멘션, 답장 핑, 또는 메시지 맨 앞의 `히나야`로 호출해 주세요.
관리·설정 기능은 Discord 슬래시 명령으로만 사용합니다.

자동 장기 기억
`/memory show` — 현재 채널에서 자동으로 요약된 내 기억 확인
`/memory clear` — 현재 서버 또는 DM에서 내 자동 대화 기억 삭제
`/memory mode` / `status` / `overview` — 봇 관리자용 자동 기억 설정
`/memory purge` — 봇 관리자용 범위별 자동 기억 초기화

수동 메모
`/note show` — 내 메모 또는 서버 공통 메모 확인
`/note set` — 내 메모 저장, 또는 서버 관리자가 서버 공통 메모 저장
`/note clear` — 내 메모 삭제, 또는 서버 관리자가 서버 공통 메모 삭제
수동 메모는 `/memory mode`와 독립적으로 유지됩니다.

최근 대화 문맥
`/chatlog mode` — 최근 채널 대화 사용 여부 설정
`/chatlog status` — 현재 채널 설정 확인
`/chatlog overview` — 전체 서버/채널 설정 확인
`/chatlog clear` — 현재 채널의 임시 최근 대화 문맥 비우기

관리
`/instruction ...` — 동적 캐릭터 지침 관리
`/knowledge ...` — runtime knowledge 관리
`/emoji add|import|list|edit|remove` — 봇 관리자용 이모지 관리

현재 호출 메시지에 포함된 지원 이미지 첨부·커스텀 이모지·래스터 스티커는 직접 볼 수 있습니다.
과거 이미지와 답장 대상 원문은 자동으로 읽지 않습니다."""


def _scope(interaction: discord.Interaction) -> Scope:
    if interaction.channel_id is None:
        raise ValueError("채널 안에서 실행해 주세요.")
    return Scope(interaction.guild_id, interaction.channel_id, interaction.user.id)


def _is_bot_admin(client, user_id: int) -> bool:
    return user_id in client.emoji_admin_ids


async def _send_ephemeral_pages(interaction: discord.Interaction, pages: list[str]):
    if not pages:
        pages = ["표시할 내용이 없어요."]
    if interaction.response.is_done():
        await interaction.followup.send(pages[0], ephemeral=True)
    else:
        await interaction.response.send_message(pages[0], ephemeral=True)
    for page in pages[1:]:
        await interaction.followup.send(page, ephemeral=True)


def _text_pages(lines: list[str], *, limit: int = 1850) -> list[str]:
    pages: list[str] = []
    current = ""
    for line in lines:
        candidate = line if not current else current + "\n" + line
        if current and len(candidate) > limit:
            pages.append(current)
            current = line
        else:
            current = candidate
    if current:
        pages.append(current)
    return pages


def _parse_emoji_import_items(items: str) -> list[tuple[str, str]]:
    parsed: list[tuple[str, str]] = []
    seen: set[str] = set()
    for line_number, raw in enumerate(items.splitlines(), 1):
        line = raw.strip()
        if not line:
            continue
        if "|" not in line:
            raise ValueError(f"{line_number}번째 줄은 `이모지이름 | 사용 상황` 형식으로 입력해 주세요.")
        alias, description = (part.strip() for part in line.split("|", 1))
        if not _EMOJI_ALIAS_RE.fullmatch(alias):
            raise ValueError(
                f"{line_number}번째 줄의 이름은 소문자로 시작하는 영문·숫자·밑줄 2~32자여야 해요."
            )
        if not 1 <= len(description) <= 100:
            raise ValueError(f"{line_number}번째 줄의 사용 상황은 1~100자로 입력해 주세요.")
        if alias in seen:
            raise ValueError(f"{line_number}번째 줄의 `{alias}`가 입력 안에서 중복됐어요.")
        seen.add(alias)
        parsed.append((alias, description))
    if not parsed:
        raise ValueError("등록할 항목을 한 줄 이상 입력해 주세요.")
    if len(parsed) > 20:
        raise ValueError("한 번에 최대 20개까지 가져올 수 있어요.")
    return parsed


def upgrade_memory_group(client):
    """Add user-facing automatic-memory commands to the base admin memory group."""
    group = client.tree.get_command("memory")
    if not isinstance(group, app_commands.Group):
        raise TypeError("/memory group is not registered")

    async def selective_check(interaction: discord.Interaction) -> bool:
        command = getattr(interaction, "command", None)
        name = getattr(command, "name", "")
        if name in _ADMIN_MEMORY_COMMANDS and not _is_bot_admin(client, interaction.user.id):
            await interaction.response.send_message(
                "봇 소유자 또는 지정된 관리자만 사용할 수 있어요.", ephemeral=True)
            return False
        return True

    group.interaction_check = selective_check

    @app_commands.command(name="show", description="현재 채널의 자동 장기 요약 확인")
    async def show(interaction: discord.Interaction):
        try:
            scope = _scope(interaction)
        except ValueError as exc:
            await interaction.response.send_message(str(exc), ephemeral=True)
            return
        summary, _ = client.store.summary(scope)
        await interaction.response.send_message(
            "이 채널에서 자동으로 요약된 기억:\n" + (summary or "아직 요약된 기억이 없어요."),
            ephemeral=True,
        )

    @app_commands.command(name="clear", description="이 서버 또는 DM에서의 내 자동 장기 기억 삭제")
    @app_commands.describe(confirm="삭제를 확인하려면 true")
    async def clear(interaction: discord.Interaction, confirm: bool):
        try:
            scope = _scope(interaction)
        except ValueError as exc:
            await interaction.response.send_message(str(exc), ephemeral=True)
            return
        if not confirm:
            await interaction.response.send_message(
                "삭제하지 않았어요. 실제로 삭제하려면 confirm을 true로 선택해 주세요.", ephemeral=True)
            return
        async with client.channel_lock(scope):
            client.store.forget(scope)
        await interaction.response.send_message(
            "이 서버 또는 DM에서 자동으로 쌓인 대화 기록과 요약을 삭제했어요. "
            "직접 저장한 /note 메모와 최근 채널 대화 문맥은 그대로 유지돼요.",
            ephemeral=True,
        )

    for command in (show, clear):
        group.add_command(command)

    return group


@app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
@app_commands.allowed_installs(guilds=True, users=True)
class EmojiSlashCommands(app_commands.Group):
    def __init__(self, client):
        super().__init__(name="emoji", description="히나가 사용할 이모지 관리 (봇 관리자 전용)")
        self.client = client

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if not _is_bot_admin(self.client, interaction.user.id):
            await interaction.response.send_message(
                "봇 소유자 또는 지정된 관리자만 사용할 수 있어요.", ephemeral=True)
            return False
        return True

    async def on_error(self, interaction: discord.Interaction, error):
        log.warning("Emoji slash command failed (%s)", type(error).__name__)
        text = "이모지 명령을 처리하지 못했어요. 잠시 후 다시 시도해 주세요."
        if interaction.response.is_done():
            await interaction.followup.send(text, ephemeral=True)
        else:
            await interaction.response.send_message(text, ephemeral=True)

    @app_commands.command(name="add", description="모델용 별칭으로 이모지 등록")
    @app_commands.describe(
        alias="모델이 사용할 별칭 (영문 소문자 시작, 2~32자)",
        description="이 이모지를 사용할 상황 (1~100자)",
        source="기존 서버 이모지 markup 또는 숫자 ID",
        image="새 application emoji로 만들 PNG/GIF/JPEG/WebP (256 KiB 이하)",
    )
    async def add(
        self,
        interaction: discord.Interaction,
        alias: str,
        description: str,
        source: str | None = None,
        image: discord.Attachment | None = None,
    ):
        if (source is None) == (image is None):
            await interaction.response.send_message(
                "source의 기존 이모지 또는 image 파일 중 하나만 지정해 주세요.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            markup = await self.client.emoji_registry.add(
                alias, description, attachment=image, source=source)
        except ValueError as exc:
            await interaction.followup.send(str(exc), ephemeral=True)
            return
        await interaction.followup.send(f"등록했어요: {markup} `:{alias}:`", ephemeral=True)

    @app_commands.command(name="import", description="현재 서버 이모지를 이름으로 여러 개 한꺼번에 등록")
    @app_commands.describe(items="줄마다 `이모지이름 | 사용 상황` 형식으로 입력 (최대 20개)")
    async def import_emojis(self, interaction: discord.Interaction, items: str):
        if interaction.guild is None:
            await interaction.response.send_message("서버에서만 사용할 수 있는 명령이에요.", ephemeral=True)
            return
        try:
            parsed = _parse_emoji_import_items(items)
        except ValueError as exc:
            await interaction.response.send_message(str(exc), ephemeral=True)
            return

        usable: dict[str, discord.Emoji] = {}
        ambiguous: set[str] = set()
        for emoji in interaction.guild.emojis:
            if not emoji.is_usable():
                continue
            if emoji.name in usable:
                ambiguous.add(emoji.name)
            else:
                usable[emoji.name] = emoji

        await interaction.response.defer(ephemeral=True, thinking=True)
        success = 0
        lines: list[str] = []
        for alias, description in parsed:
            if alias in ambiguous:
                lines.append(f"`:{alias}:` ❌ 같은 이름의 서버 이모지가 여러 개 있어요.")
                continue
            emoji = usable.get(alias)
            if emoji is None:
                lines.append(f"`:{alias}:` ❌ 같은 이름의 사용 가능한 서버 이모지를 찾지 못했어요.")
                continue
            try:
                markup = await self.client.emoji_registry.add(
                    alias, description, source=str(emoji.id))
            except ValueError as exc:
                reason = str(exc)
                lines.append(f"`:{alias}:` ❌ {reason}")
                continue
            success += 1
            lines.append(f"{markup} `:{alias}:` ✅")

        result = [f"{success}/{len(parsed)}개 등록 완료"] + lines
        await _send_ephemeral_pages(interaction, _text_pages(result))

    @app_commands.command(name="list", description="등록된 이모지와 사용 상황 목록")
    async def list_emojis(self, interaction: discord.Interaction):
        catalog = {
            e["name"]: e
            for e in await self.client.emoji_registry.catalog(getattr(interaction, "channel", None))
        }
        rows = self.client.store.emoji_rows()
        if not rows:
            await interaction.response.send_message("등록된 이모지가 없어요.", ephemeral=True)
            return
        lines = [f"히나 이모지 {len(rows)}/20"]
        for row in rows:
            preview = catalog.get(row["alias"], {}).get("markup", "(사용 불가)")
            description = discord.utils.escape_markdown(row["description"])
            lines.append(f"`:{row['alias']}:` {preview} — {description}")
        await _send_ephemeral_pages(interaction, _text_pages(lines))

    @app_commands.command(name="edit", description="등록된 이모지의 사용 상황 변경")
    @app_commands.describe(alias="수정할 별칭", description="새 사용 상황 (1~100자)")
    async def edit(self, interaction: discord.Interaction, alias: str, description: str):
        try:
            await self.client.emoji_registry.edit(alias, description)
        except ValueError as exc:
            await interaction.response.send_message(str(exc), ephemeral=True)
            return
        await interaction.response.send_message("사용 상황을 변경했어요.", ephemeral=True)

    @app_commands.command(name="remove", description="이모지를 모델 사용 목록에서 제외")
    @app_commands.describe(alias="삭제할 별칭")
    async def remove(self, interaction: discord.Interaction, alias: str):
        try:
            await self.client.emoji_registry.remove(alias)
        except ValueError as exc:
            await interaction.response.send_message(str(exc), ephemeral=True)
            return
        await interaction.response.send_message(
            "사용 목록에서 제외했어요. 원본 이모지는 삭제하지 않아요.", ephemeral=True)


def install_slash_commands(client):
    """Install the slash-only user/admin command surface on a production client."""
    upgrade_memory_group(client)
    client.tree.add_command(NoteCommands(client))
    client.tree.add_command(ChatLogCommands(client))
    client.tree.add_command(EmojiSlashCommands(client))

    @app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
    @app_commands.allowed_installs(guilds=True, users=True)
    @app_commands.command(name="help", description="히나 봇 사용법과 DM 시작 방법 보기")
    async def help_command(interaction: discord.Interaction):
        await interaction.response.send_message(HELP_TEXT, ephemeral=True)

    client.tree.add_command(help_command)

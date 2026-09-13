"""Discord slash-command surface for runtime user/admin controls.

The legacy prefix+slash parser still exists in the lower-level BaseHinaClient for old unit-test
fixtures, but the production web_bot entrypoint installs this surface and disables that parser.
"""

import asyncio
import logging

import discord
from discord import app_commands

from .chatlog_commands import ChatLogCommands
from .memory_commands import MemoryMode
from .routing import Scope

log = logging.getLogger("hina")

_ADMIN_MEMORY_COMMANDS = {"mode", "status", "overview", "purge"}

HELP_TEXT = """일반 대화는 @멘션, 답장 핑, 또는 메시지 맨 앞의 `히나야`로 호출해 주세요.
관리·설정 기능은 Discord 슬래시 명령으로만 사용합니다.

장기 기억
`/memory show` — 현재 채널의 내 요약과 개인 메모 확인
`/memory note` / `/memory note-clear` — 개인 메모 설정/삭제
`/memory clear` — 현재 서버 또는 DM에서 내 장기 기억 삭제
`/memory server-show` / `server-note` / `server-clear` — 서버 공통 메모
`/memory mode` / `status` / `overview` — 봇 관리자용 장기 기억 설정
`/memory purge` — 봇 관리자용 범위별 사용자 장기 기억 초기화

최근 대화 문맥
`/chatlog mode` — 최근 채널 대화 사용 여부 설정
`/chatlog status` — 현재 채널 설정 확인
`/chatlog overview` — 전체 서버/채널 설정 확인
`/chatlog clear` — 현재 채널의 임시 최근 대화 문맥 비우기

관리
`/instruction ...` — 동적 캐릭터 지침 관리
`/knowledge ...` — runtime knowledge 관리
`/emoji add|list|edit|remove` — 봇 관리자용 이모지 관리

일반 대화에서는 첨부파일·이미지·답장 원문을 직접 읽지 않습니다."""


def _scope(interaction: discord.Interaction) -> Scope:
    if interaction.channel_id is None:
        raise ValueError("채널 안에서 실행해 주세요.")
    return Scope(interaction.guild_id, interaction.channel_id, interaction.user.id)


def _is_bot_admin(client, user_id: int) -> bool:
    return user_id in client.emoji_admin_ids


def _can_manage_guild(interaction: discord.Interaction) -> bool:
    permissions = getattr(interaction.user, "guild_permissions", None)
    return bool(permissions and permissions.manage_guild)


def _user_lock(client, scope: Scope) -> asyncio.Lock:
    key = scope.user_note
    lock = client.locks.get(key)
    if lock is None:
        lock = asyncio.Lock()
        client.locks[key] = lock
    return lock


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


def upgrade_memory_group(client):
    """Turn the base /memory admin group into the complete user/admin memory surface."""
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

    # User-owned memory operations share /memory with bot-admin configuration operations.
    group.interaction_check = selective_check

    @app_commands.command(name="show", description="현재 채널의 내 장기 요약과 개인 메모 확인")
    async def show(interaction: discord.Interaction):
        try:
            scope = _scope(interaction)
        except ValueError as exc:
            await interaction.response.send_message(str(exc), ephemeral=True)
            return
        summary, _ = client.store.summary(scope)
        text = ("이 채널에서의 기억:\n" + (summary or "아직 요약된 기억이 없어요.")
                + "\n\n개인 메모:\n" + (client.store.note(scope.user_note) or "없어요."))
        await interaction.response.send_message(text, ephemeral=True)

    @app_commands.command(name="note", description="같은 서버의 내 응답에 사용할 개인 메모 교체")
    @app_commands.describe(text="저장할 개인 메모 (1~1500자)")
    async def note(interaction: discord.Interaction, text: str):
        try:
            scope = _scope(interaction)
        except ValueError as exc:
            await interaction.response.send_message(str(exc), ephemeral=True)
            return
        if not 1 <= len(text.strip()) <= 1500:
            await interaction.response.send_message("1~1500자의 개인 메모를 입력해 주세요.", ephemeral=True)
            return
        if not MemoryMode(client.store.memory_mode(scope)).writes:
            await interaction.response.send_message(
                "현재 모드는 새 기억 저장이 꺼져 있어요. /memory mode로 변경해 주세요.", ephemeral=True)
            return
        async with client.channel_lock(scope), _user_lock(client, scope):
            client.store.set_note(scope.user_note, text.strip())
        await interaction.response.send_message(
            "개인 메모를 저장했어요. 서버에서는 같은 서버의 다른 채널에서도 참고해요.", ephemeral=True)

    @app_commands.command(name="note-clear", description="내 개인 메모 삭제")
    async def note_clear(interaction: discord.Interaction):
        try:
            scope = _scope(interaction)
        except ValueError as exc:
            await interaction.response.send_message(str(exc), ephemeral=True)
            return
        async with client.channel_lock(scope), _user_lock(client, scope):
            client.store.set_note(scope.user_note, "")
        await interaction.response.send_message("개인 메모를 삭제했어요.", ephemeral=True)

    @app_commands.command(name="clear", description="이 서버 또는 DM에서의 내 장기 기억 삭제")
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
        async with client.channel_lock(scope), _user_lock(client, scope):
            client.store.forget(scope)
        await interaction.response.send_message(
            "이 서버 또는 DM에서의 대화 기록, 자동 요약, 개인 메모를 삭제했어요. "
            "최근 채널 대화 문맥은 그대로 유지돼요.",
            ephemeral=True,
        )

    @app_commands.command(name="server-show", description="현재 서버의 공통 메모 확인")
    async def server_show(interaction: discord.Interaction):
        try:
            scope = _scope(interaction)
        except ValueError as exc:
            await interaction.response.send_message(str(exc), ephemeral=True)
            return
        if scope.guild_id is None:
            await interaction.response.send_message("서버에서만 사용할 수 있는 명령이에요.", ephemeral=True)
            return
        await interaction.response.send_message(
            client.store.note(scope.realm) or "서버 공통 메모가 없어요.", ephemeral=True)

    @app_commands.command(name="server-note", description="현재 서버의 공통 메모 교체 (서버 관리자)")
    @app_commands.describe(text="저장할 서버 공통 메모 (1~1500자)")
    async def server_note(interaction: discord.Interaction, text: str):
        try:
            scope = _scope(interaction)
        except ValueError as exc:
            await interaction.response.send_message(str(exc), ephemeral=True)
            return
        if scope.guild_id is None:
            await interaction.response.send_message("서버에서만 사용할 수 있는 명령이에요.", ephemeral=True)
            return
        if not _can_manage_guild(interaction):
            await interaction.response.send_message("서버 관리 권한이 필요해요.", ephemeral=True)
            return
        if not 1 <= len(text.strip()) <= 1500:
            await interaction.response.send_message("1~1500자의 서버 공통 메모를 입력해 주세요.", ephemeral=True)
            return
        if not MemoryMode(client.store.memory_mode(scope)).writes:
            await interaction.response.send_message(
                "현재 모드는 새 기억 저장이 꺼져 있어요. /memory mode로 변경해 주세요.", ephemeral=True)
            return
        async with client.channel_lock(scope):
            client.store.set_note(scope.realm, text.strip())
        await interaction.response.send_message(
            "서버 공통 메모를 교체했어요. 서버 전체에서 참고해요.", ephemeral=True)

    @app_commands.command(name="server-clear", description="현재 서버의 공통 메모 삭제 (서버 관리자)")
    async def server_clear(interaction: discord.Interaction):
        try:
            scope = _scope(interaction)
        except ValueError as exc:
            await interaction.response.send_message(str(exc), ephemeral=True)
            return
        if scope.guild_id is None:
            await interaction.response.send_message("서버에서만 사용할 수 있는 명령이에요.", ephemeral=True)
            return
        if not _can_manage_guild(interaction):
            await interaction.response.send_message("서버 관리 권한이 필요해요.", ephemeral=True)
            return
        async with client.channel_lock(scope):
            client.store.set_note(scope.realm, "")
        await interaction.response.send_message("서버 공통 메모를 삭제했어요.", ephemeral=True)

    for command in (show, note, note_clear, clear, server_show, server_note, server_clear):
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
            text = str(exc).replace("히나야 /이모지 수정", "/emoji edit")
            await interaction.followup.send(text, ephemeral=True)
            return
        await interaction.followup.send(f"등록했어요: {markup} `:{alias}:`", ephemeral=True)

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
    client.tree.add_command(ChatLogCommands(client))
    client.tree.add_command(EmojiSlashCommands(client))

    @app_commands.command(name="help", description="히나 봇 사용법과 관리 명령 보기")
    async def help_command(interaction: discord.Interaction):
        await interaction.response.send_message(HELP_TEXT, ephemeral=True)

    client.tree.add_command(help_command)
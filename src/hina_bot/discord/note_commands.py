"""Manual persistent notes, separate from automatically accumulated memory."""

import asyncio

import discord
from discord import app_commands

from .routing import Scope

_SCOPE_CHOICES = [
    app_commands.Choice(name="내 메모", value="me"),
    app_commands.Choice(name="서버 공통 메모", value="server"),
]


def _scope(interaction: discord.Interaction) -> Scope:
    if interaction.channel_id is None:
        raise ValueError("채널 안에서 실행해 주세요.")
    return Scope(interaction.guild_id, interaction.channel_id, interaction.user.id)


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


@app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
@app_commands.allowed_installs(guilds=True, users=True)
class NoteCommands(app_commands.Group):
    def __init__(self, client):
        super().__init__(name="note", description="직접 저장하는 개인·서버 메모 관리")
        self.client = client

    @staticmethod
    def _key(scope: Scope, target: str) -> str:
        if target == "me":
            return scope.user_note
        if target == "server":
            if scope.guild_id is None:
                raise ValueError("DM에서는 서버 공통 메모를 사용할 수 없어요.")
            return scope.realm
        raise ValueError("알 수 없는 메모 범위예요.")

    @app_commands.command(name="show", description="직접 저장한 개인 또는 서버 공통 메모 확인")
    @app_commands.describe(scope="확인할 메모. 기본은 내 메모")
    @app_commands.choices(scope=_SCOPE_CHOICES)
    async def show(self, interaction: discord.Interaction, scope: str = "me"):
        try:
            current = _scope(interaction)
            key = self._key(current, scope)
        except ValueError as exc:
            await interaction.response.send_message(str(exc), ephemeral=True)
            return
        value = self.client.store.note(key)
        label = "내 메모" if scope == "me" else "서버 공통 메모"
        await interaction.response.send_message(
            f"{label}:\n{value or '없어요.'}", ephemeral=True)

    @app_commands.command(name="set", description="개인 또는 서버 공통 메모 저장·교체")
    @app_commands.describe(
        text="저장할 메모 (1~1500자)",
        scope="저장할 범위. 기본은 내 메모",
    )
    @app_commands.choices(scope=_SCOPE_CHOICES)
    async def set_note(self, interaction: discord.Interaction, text: str, scope: str = "me"):
        try:
            current = _scope(interaction)
            key = self._key(current, scope)
        except ValueError as exc:
            await interaction.response.send_message(str(exc), ephemeral=True)
            return
        text = text.strip()
        if not 1 <= len(text) <= 1500:
            await interaction.response.send_message("1~1500자의 메모를 입력해 주세요.", ephemeral=True)
            return
        if scope == "server" and not _can_manage_guild(interaction):
            await interaction.response.send_message("서버 관리 권한이 필요해요.", ephemeral=True)
            return

        if scope == "me":
            async with self.client.channel_lock(current), _user_lock(self.client, current):
                self.client.store.set_note(key, text)
            message = "내 메모를 저장했어요. 서버에서는 같은 서버의 다른 채널에서도 참고해요."
        else:
            async with self.client.channel_lock(current):
                self.client.store.set_note(key, text)
            message = "서버 공통 메모를 저장했어요. 서버 전체에서 참고해요."
        await interaction.response.send_message(message, ephemeral=True)

    @app_commands.command(name="clear", description="개인 또는 서버 공통 메모 삭제")
    @app_commands.describe(scope="삭제할 범위. 기본은 내 메모")
    @app_commands.choices(scope=_SCOPE_CHOICES)
    async def clear(self, interaction: discord.Interaction, scope: str = "me"):
        try:
            current = _scope(interaction)
            key = self._key(current, scope)
        except ValueError as exc:
            await interaction.response.send_message(str(exc), ephemeral=True)
            return
        if scope == "server" and not _can_manage_guild(interaction):
            await interaction.response.send_message("서버 관리 권한이 필요해요.", ephemeral=True)
            return

        if scope == "me":
            async with self.client.channel_lock(current), _user_lock(self.client, current):
                self.client.store.set_note(key, "")
            message = "내 메모를 삭제했어요."
        else:
            async with self.client.channel_lock(current):
                self.client.store.set_note(key, "")
            message = "서버 공통 메모를 삭제했어요."
        await interaction.response.send_message(message, ephemeral=True)

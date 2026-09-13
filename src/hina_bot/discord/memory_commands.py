"""Persistent-memory controls for users and bot administrators."""
import logging
from enum import Enum

import discord
from discord import app_commands

from .admin_list import MAX_DISCORD_TEXT, table_row
from .instruction_commands import InstructionCommands
from .knowledge_commands import KnowledgeCommands
from .routing import Scope

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
_VIEW_CHOICES = [
    app_commands.Choice(name="모든 서버/채널", value="all"),
    app_commands.Choice(name="직접 설정된 override 중심", value="overrides"),
]
_PURGE_TARGET_CHOICES = [
    app_commands.Choice(name="현재 채널의 모든 사용자 기억", value="channel"),
    app_commands.Choice(name="현재 서버의 모든 사용자 기억", value="server"),
    app_commands.Choice(name="모든 서버/DM의 사용자 기억", value="global"),
]
_SOURCE_LABEL = {"channel": "채널", "server": "서버", "global": "전역", "default": "기본값"}


class MemoryMode(str, Enum):
    normal = "normal"
    read_only = "read_only"
    write_only = "write_only"
    off = "off"

    @property
    def reads(self):
        return self in (MemoryMode.normal, MemoryMode.read_only)

    @property
    def writes(self):
        return self in (MemoryMode.normal, MemoryMode.write_only)


def _table_pages(title: str, columns: list[str], rows: list[list[str]], widths: list[int]) -> list[str]:
    heading = [table_row(columns, widths), table_row(["-" * width for width in widths], widths)]
    groups: list[list[str]] = []
    current: list[str] = []
    budget = MAX_DISCORD_TEXT - len(title) - 40
    for values in rows:
        line = table_row(values, widths)
        candidate = "\n".join(heading + current + [line])
        if current and len(candidate) > budget:
            groups.append(current)
            current = []
        current.append(line)
    groups.append(current)
    count = len(groups)
    return [
        f"{title} ({index}/{count})\n```text\n" + "\n".join(heading + lines) + "\n```"
        for index, lines in enumerate(groups, 1)
    ]


@app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
@app_commands.allowed_installs(guilds=True, users=True)
class MemoryCommands(app_commands.Group):
    def __init__(self, client):
        super().__init__(name="memory", description="장기 기억 관리")
        self.client = client
        if hasattr(client, "tree") and hasattr(client, "settings"):
            client.tree.add_command(InstructionCommands(client))
            client.tree.add_command(KnowledgeCommands(client))

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
        text = "기억 설정을 처리하지 못했어요. /memory status로 현재 상태를 확인해 주세요."
        if interaction.response.is_done():
            await interaction.followup.send(text, ephemeral=True)
        else:
            await interaction.response.send_message(text, ephemeral=True)

    @staticmethod
    def _target_key(scope: Scope, target: str) -> str:
        if target == "global":
            return "global"
        if target == "server":
            if scope.guild_id is None:
                raise ValueError("DM에서는 서버 설정을 변경할 수 없어요.")
            return scope.realm
        if target == "channel":
            return scope.channel
        raise ValueError("알 수 없는 설정 범위예요.")

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
    )
    @app_commands.choices(value=_VALUE_CHOICES, target=_TARGET_CHOICES)
    async def mode(
        self,
        interaction: discord.Interaction,
        value: str,
        target: str = "channel",
    ):
        try:
            scope = self.scope(interaction)
            key = self._target_key(scope, target)
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
        state = "상위 설정을 따르도록 변경" if value == "inherit" else f"{value}로 변경"
        await interaction.followup.send(
            f"{changed} 장기 기억 설정을 {state}했어요.\n\n{self._status_text(scope)}", ephemeral=True)

    @app_commands.command(name="status", description="현재 채널의 장기 기억 설정 확인")
    async def status(self, interaction: discord.Interaction):
        try:
            text = self._status_text(self.scope(interaction))
        except ValueError as exc:
            await interaction.response.send_message(str(exc), ephemeral=True)
            return
        await interaction.response.send_message(text, ephemeral=True)

    def _overview_rows(self, user_id: int, view: str) -> list[list[str]]:
        store = self.client.store
        overrides = store.memory_mode_overrides()
        global_mode = overrides.get("global")
        rows = [["전역", "GLOBAL", global_mode or "기본(normal)", global_mode or "normal"]]
        known = {"global"}
        settings = getattr(self.client, "settings", None)
        allowed = getattr(settings, "allowed_guild_ids", frozenset()) if settings else frozenset()
        guilds = sorted(getattr(self.client, "guilds", []), key=lambda guild: guild.name.casefold())
        for guild in guilds:
            if allowed and guild.id not in allowed:
                continue
            server_key = f"guild:{guild.id}"
            server_mode = overrides.get(server_key)
            server_effective = server_mode or global_mode or "normal"
            known.add(server_key)
            rows.append(["서버", guild.name, server_mode or "상속", server_effective])

            channels = list(getattr(guild, "text_channels", [])) + list(getattr(guild, "threads", []))
            channels = sorted(
                {channel.id: channel for channel in channels}.values(),
                key=lambda channel: channel.name.casefold(),
            )
            inherited = 0
            for channel in channels:
                scope = Scope(guild.id, channel.id, user_id)
                direct = overrides.get(scope.channel)
                known.add(scope.channel)
                if view == "overrides" and direct is None:
                    inherited += 1
                    continue
                rows.append([
                    "채널", f"{guild.name}/#{channel.name}",
                    direct or "상속", direct or server_effective,
                ])
            if view == "overrides" and inherited:
                rows.append([
                    "채널", f"{guild.name}/(나머지 {inherited}개)", "상속", server_effective,
                ])

        for key in sorted(set(overrides) - known):
            mode = overrides[key]
            rows.append(["미확인", key, mode, mode])
        return rows

    @app_commands.command(name="overview", description="모든 서버/채널의 장기 기억 설정 한눈에 보기")
    @app_commands.describe(view="모든 채널을 보거나 직접 override된 항목 중심으로 압축해서 보기")
    @app_commands.choices(view=_VIEW_CHOICES)
    async def overview(self, interaction: discord.Interaction, view: str = "all"):
        if view not in {choice.value for choice in _VIEW_CHOICES}:
            await interaction.response.send_message("알 수 없는 보기 방식이에요.", ephemeral=True)
            return
        rows = self._overview_rows(interaction.user.id, view)
        pages = _table_pages(
            "장기 기억 설정 · 직접=직접 저장, 적용=상속까지 계산한 최종 값",
            ["범위", "서버/채널", "직접", "적용"],
            rows,
            [6, 40, 14, 14],
        )
        await interaction.response.send_message(pages[0], ephemeral=True)
        for page in pages[1:]:
            await interaction.followup.send(page, ephemeral=True)

    @app_commands.command(name="purge", description="선택한 범위의 모든 사용자 장기 기억 삭제")
    @app_commands.describe(
        target="삭제 범위",
        confirm="실제 삭제를 확인하려면 true",
    )
    @app_commands.choices(target=_PURGE_TARGET_CHOICES)
    async def purge(
        self,
        interaction: discord.Interaction,
        target: str = "channel",
        confirm: bool = False,
    ):
        try:
            scope = self.scope(interaction)
            if target not in {choice.value for choice in _PURGE_TARGET_CHOICES}:
                raise ValueError("알 수 없는 삭제 범위예요.")
            if target == "server" and scope.guild_id is None:
                raise ValueError("DM에서는 서버 전체 기억을 삭제할 수 없어요.")
        except ValueError as exc:
            await interaction.response.send_message(str(exc), ephemeral=True)
            return
        if not confirm:
            await interaction.response.send_message(
                "삭제하지 않았어요. 실제로 삭제하려면 confirm을 true로 선택해 주세요.", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)
        async with self.client.channel_lock(scope):
            if target == "channel":
                deleted = self.client.store.purge_channel_memory(scope)
                label = "현재 채널"
            elif target == "server":
                deleted = self.client.store.purge_realm_memory(scope)
                label = "현재 서버"
            else:
                deleted = self.client.store.purge_all_memory()
                label = "전체"
        await interaction.followup.send(
            f"{label}의 사용자 장기 기억을 초기화했어요. 삭제된 저장 항목: {deleted}개. "
            "서버 공통 메모, 기억 모드 설정, 최근 채널 로그는 유지돼요.",
            ephemeral=True,
        )
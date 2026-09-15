"""Ephemeral recent-channel-context controls for bot administrators."""
import logging

import discord
from discord import app_commands

from hina_bot.core.admin_list import MAX_DISCORD_TEXT, table_row
from hina_bot.core.routing import Scope

from .chatlog_capture import capture_mode_overrides, set_capture_mode_override

log = logging.getLogger("hina")
_TARGET_CHOICES = [
    app_commands.Choice(name="현재 채널", value="channel"),
    app_commands.Choice(name="현재 서버", value="server"),
    app_commands.Choice(name="전역", value="global"),
]
_VALUE_CHOICES = [
    app_commands.Choice(name="all — 같은 채널의 일반 대화까지 포함", value="all"),
    app_commands.Choice(name="direct — 히나에게 직접 말한 대화만", value="direct"),
    app_commands.Choice(name="off — 최근 채널 대화 문맥 사용 안 함", value="off"),
    app_commands.Choice(name="inherit — 상위 설정 따르기", value="inherit"),
]
_VIEW_CHOICES = [
    app_commands.Choice(name="직접 설정만 (기본)", value="overrides"),
    app_commands.Choice(name="전체 상속 결과", value="all"),
]
_SOURCE_LABEL = {"channel": "채널", "server": "서버", "global": "전역", "default": "기본값"}
_MIGRATION_MARKER = "config:chatlog_unified_v1"


def _table_pages(title, columns, rows, widths):
    heading = [table_row(columns, widths), table_row(["-" * width for width in widths], widths)]
    groups, current = [], []
    budget = MAX_DISCORD_TEXT - len(title) - 40
    for values in rows:
        line = table_row(values, widths)
        if current and len("\n".join(heading + current + [line])) > budget:
            groups.append(current)
            current = []
        current.append(line)
    groups.append(current)
    count = len(groups)
    return [
        f"{title} ({index}/{count})\n```text\n" + "\n".join(heading + lines) + "\n```"
        for index, lines in enumerate(groups, 1)
    ]


def _scope_chain(key):
    if key == "global":
        return ["global"]
    if key.startswith("guild:") and ":channel:" in key:
        return ["global", key.split(":channel:", 1)[0], key]
    if key.startswith("guild:"):
        return ["global", key]
    return ["global", key]


def _effective(mapping, key, default):
    value = default
    for part in _scope_chain(key):
        value = mapping.get(part, value)
    return value


def _migrate_legacy_settings(store):
    if store.note(_MIGRATION_MARKER) == "1":
        return
    logs = store.chat_log_mode_overrides()
    captures = capture_mode_overrides(store)
    combined = {}
    for key in set(logs) | set(captures):
        enabled = _effective(logs, key, "on")
        capture = _effective(captures, key, "all")
        combined[key] = "off" if enabled == "off" else capture
    with store.db:
        store.db.execute("DELETE FROM chat_log_modes")
        store.db.execute("DELETE FROM notes WHERE scope LIKE 'config:chatlog_capture:%'")
    for key, mode in combined.items():
        store.set_chat_log_mode_override(key, "off" if mode == "off" else "on")
        set_capture_mode_override(store, key, "all" if mode == "off" else mode)
    store.set_note(_MIGRATION_MARKER, "1")


def _mode_override(store, key):
    log_mode = store.chat_log_mode_override(key)
    capture = capture_mode_overrides(store).get(key)
    if log_mode is None and capture is None:
        return None
    if log_mode == "off":
        return "off"
    return capture or "all"


def _mode_chain(store, scope):
    global_mode = _mode_override(store, "global")
    server_mode = _mode_override(store, scope.realm) if scope.guild_id is not None else None
    channel_mode = _mode_override(store, scope.channel)
    if channel_mode is not None:
        effective, source = channel_mode, "channel"
    elif server_mode is not None:
        effective, source = server_mode, "server"
    elif global_mode is not None:
        effective, source = global_mode, "global"
    else:
        effective, source = "all", "default"
    return {"global": global_mode, "server": server_mode, "channel": channel_mode,
            "effective": effective, "source": source}


def _set_mode_override(store, key, mode):
    if mode is not None and mode not in {"all", "direct", "off"}:
        raise ValueError("Invalid chat log mode")
    if mode is None:
        store.set_chat_log_mode_override(key, None)
        set_capture_mode_override(store, key, None)
    else:
        store.set_chat_log_mode_override(key, "off" if mode == "off" else "on")
        set_capture_mode_override(store, key, "all" if mode == "off" else mode)


@app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
@app_commands.allowed_installs(guilds=True, users=True)
class ChatLogCommands(app_commands.Group):
    def __init__(self, client):
        super().__init__(name="chatlog", description="최근 채널 대화 문맥 관리 (봇 관리자 전용)")
        self.client = client
        _migrate_legacy_settings(client.store)

    async def interaction_check(self, interaction):
        if interaction.user.id not in self.client.emoji_admin_ids:
            await interaction.response.send_message("봇 소유자 또는 지정된 관리자만 사용할 수 있어요.", ephemeral=True)
            return False
        return True

    @staticmethod
    def scope(interaction):
        if interaction.channel_id is None:
            raise ValueError("채널 안에서 실행해 주세요.")
        return Scope(interaction.guild_id, interaction.channel_id, interaction.user.id)

    async def on_error(self, interaction, error):
        log.warning("Chatlog command failed (%s)", type(error).__name__)
        text = "최근 대화 문맥 설정을 처리하지 못했어요. /chatlog status로 현재 상태를 확인해 주세요."
        if interaction.response.is_done():
            await interaction.followup.send(text, ephemeral=True)
        else:
            await interaction.response.send_message(text, ephemeral=True)

    @staticmethod
    def _target_key(scope, target):
        if target == "global":
            return "global"
        if target == "server":
            if scope.guild_id is None:
                raise ValueError("DM에서는 서버 설정을 변경할 수 없어요.")
            return scope.realm
        if target == "channel":
            return scope.channel
        raise ValueError("알 수 없는 설정 범위예요.")

    def _status_text(self, scope):
        chain = _mode_chain(self.client.store, scope)
        global_text = chain["global"] or "all (기본값)"
        lines = [f"최종 적용: **{chain['effective']}** (출처: {_SOURCE_LABEL[chain['source']]})",
                 f"전역: `{global_text}`"]
        parent = chain["global"] or "all"
        if scope.guild_id is not None:
            server = chain["server"]
            lines.append(f"서버: `{'상속 → ' + parent if server is None else server}`")
            parent = server or parent
        channel = chain["channel"]
        lines.append(f"채널: `{'상속 → ' + parent if channel is None else channel}`")
        if scope.guild_id is None:
            lines.append("DM에서는 최근 채널 대화 문맥을 사용하지 않아요.")
        elif chain["effective"] == "all":
            lines.append("같은 채널의 일반 대화까지 최근 문맥으로 수집·사용해요.")
        elif chain["effective"] == "direct":
            lines.append("히나에게 직접 말을 건 대화 중심으로만 최근 문맥을 수집·사용해요.")
        else:
            lines.append("최근 채널 대화 문맥을 수집하거나 사용하지 않아요.")
        return "최근 채널 대화 문맥\n" + "\n".join(lines)

    @app_commands.command(name="mode", description="최근 대화 문맥 범위를 all/direct/off 또는 상속으로 설정")
    @app_commands.describe(value="all/direct/off 또는 상위 설정 상속", target="적용 범위. 기본은 현재 채널")
    @app_commands.choices(value=_VALUE_CHOICES, target=_TARGET_CHOICES)
    async def mode(self, interaction: discord.Interaction, value: str, target: str = "channel"):
        try:
            scope = self.scope(interaction)
            key = self._target_key(scope, target)
            if value == "inherit" and target == "global":
                raise ValueError("전역 chatlog 설정은 상속할 수 없어요. all/direct/off 중 하나를 선택해 주세요.")
            if value not in {choice.value for choice in _VALUE_CHOICES}:
                raise ValueError("알 수 없는 chatlog 모드예요.")
        except ValueError as exc:
            await interaction.response.send_message(str(exc), ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        async with self.client.channel_lock(scope):
            _set_mode_override(self.client.store, key, None if value == "inherit" else value)
            if target == "global":
                self.client.recent.clear_all()
            elif target == "server":
                self.client.recent.forget(scope)
            else:
                self.client.recent.clear_channel(scope)
        changed = {"channel": "채널", "server": "서버", "global": "전역"}[target]
        state = "상위 설정을 따르도록 변경" if value == "inherit" else f"{value}으로 변경"
        await interaction.followup.send(f"{changed} 최근 대화 문맥 설정을 {state}했어요.\n\n{self._status_text(scope)}", ephemeral=True)

    @app_commands.command(name="status", description="현재 채널의 최근 대화 문맥 설정 확인")
    async def status(self, interaction: discord.Interaction):
        try:
            text = self._status_text(self.scope(interaction))
        except ValueError as exc:
            await interaction.response.send_message(str(exc), ephemeral=True)
            return
        await interaction.response.send_message(text, ephemeral=True)

    def _overview_rows(self, user_id, view):
        store = self.client.store
        override_keys = set(store.chat_log_mode_overrides()) | set(capture_mode_overrides(store))
        global_mode = _mode_override(store, "global")
        rows = [["전역", "GLOBAL", global_mode or "기본(all)", global_mode or "all"]]
        known = {"global"}
        allowed = getattr(getattr(self.client, "settings", None), "allowed_guild_ids", frozenset())
        for guild in sorted(getattr(self.client, "guilds", []), key=lambda item: item.name.casefold()):
            if allowed and guild.id not in allowed:
                continue
            server_key = f"guild:{guild.id}"
            server_mode = _mode_override(store, server_key)
            server_effective = server_mode or global_mode or "all"
            known.add(server_key)
            if view == "all" or server_key in override_keys:
                rows.append(["서버", guild.name, server_mode or "상속", server_effective])
            channels = list(getattr(guild, "text_channels", [])) + list(getattr(guild, "threads", []))
            for channel in sorted({c.id: c for c in channels}.values(), key=lambda item: item.name.casefold()):
                scope = Scope(guild.id, channel.id, user_id)
                direct = _mode_override(store, scope.channel)
                known.add(scope.channel)
                if view == "overrides" and scope.channel not in override_keys:
                    continue
                rows.append(["채널", f"{guild.name}/#{channel.name}", direct or "상속", direct or server_effective])
        for key in sorted(override_keys - known):
            mode = _mode_override(store, key) or "?"
            rows.append(["미확인", key, mode, mode])
        return rows

    @app_commands.command(name="overview", description="최근 대화 문맥 설정을 한눈에 보기")
    @app_commands.describe(view="기본은 직접 설정만 표시하며, 필요하면 전체 상속 결과를 볼 수 있어요")
    @app_commands.choices(view=_VIEW_CHOICES)
    async def overview(self, interaction: discord.Interaction, view: str = "overrides"):
        if view not in {choice.value for choice in _VIEW_CHOICES}:
            await interaction.response.send_message("알 수 없는 보기 방식이에요.", ephemeral=True)
            return
        rows = self._overview_rows(interaction.user.id, view)
        title = "최근 대화 문맥 설정 · 직접 override만 표시 (상속 항목 숨김)" if view == "overrides" else "최근 대화 문맥 설정 · 전체 상속 결과"
        pages = _table_pages(title, ["범위", "서버/채널", "직접", "적용"], rows, [6, 40, 14, 14])
        await interaction.response.send_message(pages[0], ephemeral=True)
        for page in pages[1:]:
            await interaction.followup.send(page, ephemeral=True)

    @app_commands.command(name="clear", description="현재 채널의 임시 최근 대화 문맥 비우기")
    async def clear(self, interaction: discord.Interaction):
        try:
            scope = self.scope(interaction)
        except ValueError as exc:
            await interaction.response.send_message(str(exc), ephemeral=True)
            return
        async with self.client.channel_lock(scope):
            self.client.recent.clear_channel(scope)
        await interaction.response.send_message("현재 채널의 임시 최근 대화 문맥을 비웠어요. 장기 기억은 그대로 유지돼요.", ephemeral=True)

"""Install /chatlog capture without coupling capture policy to the base command group."""

import discord
from discord import app_commands

from .chatlog_capture import (
    capture_mode_chain,
    capture_mode_overrides,
    set_capture_mode_override,
)
from .chatlog_commands import _table_pages
from .routing import Scope

_CAPTURE_CHOICES = [
    app_commands.Choice(name="all — 같은 채널의 일반 대화까지 포함", value="all"),
    app_commands.Choice(name="direct — 히나에게 직접 말한 대화와 히나 답변만", value="direct"),
    app_commands.Choice(name="inherit — 상위 설정 따르기", value="inherit"),
]
_TARGET_CHOICES = [
    app_commands.Choice(name="현재 채널", value="channel"),
    app_commands.Choice(name="현재 서버", value="server"),
    app_commands.Choice(name="전역", value="global"),
]
_VIEW_CHOICES = [
    app_commands.Choice(name="직접 설정만 (기본)", value="overrides"),
    app_commands.Choice(name="전체 상속 결과", value="all"),
]
_SOURCE_LABEL = {"channel": "채널", "server": "서버", "global": "전역", "default": "기본값"}


def _status_text(group, scope) -> str:
    chain = capture_mode_chain(group.client.store, scope)
    lines = group._chain_lines(chain, "all", include_server=scope.guild_id is not None)
    lines[0] = (
        f"최종 수집 범위: **{chain['effective']}** "
        f"(출처: {_SOURCE_LABEL[chain['source']]})"
    )
    return "최근 대화 수집 범위\n" + "\n".join(lines)


def overview_rows(group, user_id: int, view: str) -> list[list[str]]:
    store = group.client.store
    log_overrides = store.chat_log_mode_overrides()
    capture_overrides = capture_mode_overrides(store)
    global_log = log_overrides.get("global")
    global_capture = capture_overrides.get("global")
    rows = [[
        "전역", "GLOBAL",
        global_log or "기본", global_log or "on",
        global_capture or "기본", global_capture or "all",
    ]]
    known = {"global"}
    settings = getattr(group.client, "settings", None)
    allowed = getattr(settings, "allowed_guild_ids", frozenset()) if settings else frozenset()
    guilds = sorted(getattr(group.client, "guilds", []), key=lambda guild: guild.name.casefold())
    for guild in guilds:
        if allowed and guild.id not in allowed:
            continue
        server_key = f"guild:{guild.id}"
        server_log = log_overrides.get(server_key)
        server_capture = capture_overrides.get(server_key)
        server_log_effective = server_log or global_log or "on"
        server_capture_effective = server_capture or global_capture or "all"
        known.add(server_key)
        if view == "all" or server_log is not None or server_capture is not None:
            rows.append([
                "서버", guild.name,
                server_log or "상속", server_log_effective,
                server_capture or "상속", server_capture_effective,
            ])

        channels = list(getattr(guild, "text_channels", [])) + list(getattr(guild, "threads", []))
        channels = sorted(
            {channel.id: channel for channel in channels}.values(),
            key=lambda channel: channel.name.casefold(),
        )
        for channel in channels:
            scope = Scope(guild.id, channel.id, user_id)
            direct_log = log_overrides.get(scope.channel)
            direct_capture = capture_overrides.get(scope.channel)
            known.add(scope.channel)
            if view == "overrides" and direct_log is None and direct_capture is None:
                continue
            rows.append([
                "채널", f"{guild.name}/#{channel.name}",
                direct_log or "상속", direct_log or server_log_effective,
                direct_capture or "상속", direct_capture or server_capture_effective,
            ])

    unknown = (set(log_overrides) | set(capture_overrides)) - known
    for key in sorted(unknown):
        direct_log = log_overrides.get(key)
        direct_capture = capture_overrides.get(key)
        rows.append([
            "미확인", key,
            direct_log or "-", direct_log or "?",
            direct_capture or "-", direct_capture or "?",
        ])
    return rows


def install_chatlog_capture(client) -> None:
    group = client.tree.get_command("chatlog")
    if not isinstance(group, app_commands.Group):
        raise TypeError("/chatlog group is not registered")

    base_status = group._status_text

    def combined_status(scope):
        return base_status(scope) + "\n\n" + _status_text(group, scope)

    group._status_text = combined_status

    @app_commands.command(name="capture", description="최근 대화에 포함할 메시지 범위 설정")
    @app_commands.describe(
        value="all/direct 또는 상위 설정 상속",
        target="적용 범위. 기본은 현재 채널",
    )
    @app_commands.choices(value=_CAPTURE_CHOICES, target=_TARGET_CHOICES)
    async def capture(
        interaction: discord.Interaction,
        value: str,
        target: str = "channel",
    ):
        try:
            scope = group.scope(interaction)
            key = group._target_key(scope, target)
            if value == "inherit" and target == "global":
                raise ValueError("전역 capture 설정은 상속할 수 없어요. all 또는 direct를 선택해 주세요.")
            if value not in {choice.value for choice in _CAPTURE_CHOICES}:
                raise ValueError("알 수 없는 capture 모드예요.")
        except ValueError as exc:
            await interaction.response.send_message(str(exc), ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)
        async with client.channel_lock(scope):
            set_capture_mode_override(client.store, key, None if value == "inherit" else value)
            # Policy changes invalidate the in-memory buffer so stale broader context cannot linger.
            if target == "global":
                client.recent.clear_all()
            elif target == "server":
                client.recent.forget(scope)
            else:
                client.recent.clear_channel(scope)

        changed = {"channel": "채널", "server": "서버", "global": "전역"}[target]
        state = "상위 설정을 따르도록 변경" if value == "inherit" else f"{value}으로 변경"
        await interaction.followup.send(
            f"{changed} 최근 대화 수집 범위를 {state}했어요.\n\n{group._status_text(scope)}",
            ephemeral=True,
        )

    group.add_command(capture)

    group.remove_command("overview")

    @app_commands.command(name="overview", description="최근 대화 문맥 설정을 한눈에 보기")
    @app_commands.describe(view="기본은 직접 설정만 표시하며, 필요하면 전체 상속 결과를 볼 수 있어요")
    @app_commands.choices(view=_VIEW_CHOICES)
    async def overview(interaction: discord.Interaction, view: str = "overrides"):
        if view not in {choice.value for choice in _VIEW_CHOICES}:
            await interaction.response.send_message("알 수 없는 보기 방식이에요.", ephemeral=True)
            return
        rows = overview_rows(group, interaction.user.id, view)
        title = (
            "최근 대화 문맥 설정 · 직접 override만 표시 (상속 항목 숨김)"
            if view == "overrides"
            else "최근 대화 문맥 설정 · 전체 상속 결과"
        )
        pages = _table_pages(
            title,
            ["범위", "서버/채널", "로그 직접", "로그 적용", "수집 직접", "수집 적용"],
            rows,
            [6, 32, 10, 10, 10, 10],
        )
        await interaction.response.send_message(pages[0], ephemeral=True)
        for page in pages[1:]:
            await interaction.followup.send(page, ephemeral=True)

    group.add_command(overview)

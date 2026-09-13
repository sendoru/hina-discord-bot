"""Install /chatlog capture without coupling capture policy to the base command group."""

import discord
from discord import app_commands

from .chatlog_capture import capture_mode_chain, set_capture_mode_override

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
_SOURCE_LABEL = {"channel": "채널", "server": "서버", "global": "전역", "default": "기본값"}


def _status_text(group, scope) -> str:
    chain = capture_mode_chain(group.client.store, scope)
    lines = group._chain_lines(chain, "all", include_server=scope.guild_id is not None)
    lines[0] = (
        f"최종 수집 범위: **{chain['effective']}** "
        f"(출처: {_SOURCE_LABEL[chain['source']]})"
    )
    return "최근 대화 수집 범위\n" + "\n".join(lines)


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

"""Read-only, effective Discord context state for bot administrators."""

import logging

import discord
from discord import app_commands

from hina_bot.ai.egress_policy import strict_policy
from hina_bot.core.routing import Scope
from hina_bot.core.scope_overrides import (
    effective_recent_context_mode,
    memory_mode_capabilities,
    resolve_scope_chain,
)

from .chatlog_capture import capture_mode_overrides
from .scope_targets import command_target_scope

log = logging.getLogger("hina")
_SOURCE_LABELS = {"default": "기본값", "global": "전역", "server": "서버", "channel": "채널"}


def _chain_details(chain: dict, *, default: str, include_server: bool) -> list[str]:
    """Show direct overrides and inheritance without confusing direct with effective."""
    result = [f"전역: `{chain['global'] or f'{default} (기본값)'}`"]
    inherited = chain["global"] or default
    if include_server:
        direct = chain["server"]
        result.append(f"서버: `{direct if direct is not None else '상속 → ' + inherited}`")
        inherited = direct if direct is not None else inherited
    direct = chain["channel"]
    result.append(f"채널: `{direct if direct is not None else '상속 → ' + inherited}`")
    return result


def effective_state_text(
    store, scope: Scope, *, external_context_policy: str | None = None
) -> str:
    """Mirror Dashboard's independent chatlog enable/capture chains and egress policy."""
    memory = store.memory_mode_chain(scope)
    enabled = resolve_scope_chain(
        store.chat_log_mode_overrides(), scope, default="on"
    )
    capture = resolve_scope_chain(
        capture_mode_overrides(store), scope, default="all"
    )
    reads, writes = memory_mode_capabilities(str(memory["effective"]))
    actual_recent = effective_recent_context_mode(
        scope,
        chat_log_mode=str(enabled["effective"]),
        capture_mode=str(capture["effective"]),
    )
    user_note = bool(store.note(scope.user_note))
    server_note = bool(store.note(scope.realm)) if scope.guild_id is not None else False

    lines = [
        (
            f"대상: {'DM' if scope.guild_id is None else f'<#{scope.channel_id}>'}"
            f" / 사용자 ID: `{scope.user_id}`"
        ),
        "자동 장기 기억",
        f"최종 적용: **{memory['effective']}** (출처: {_SOURCE_LABELS[memory['source']]})",
        f"읽기: {'켜짐' if reads else '꺼짐'} / 쓰기: {'켜짐' if writes else '꺼짐'}",
        *_chain_details(memory, default="normal", include_server=scope.guild_id is not None),
        "",
        "최근 채널 대화 문맥 (chatlog)",
        f"활성화 설정: **{enabled['effective']}** (출처: {_SOURCE_LABELS[enabled['source']]})",
        f"수집 범위 설정: **{capture['effective']}** (출처: {_SOURCE_LABELS[capture['source']]})",
        (
            f"로컬 최근 문맥: **{actual_recent}**"
            + (" (DM에서는 사용하지 않음)" if scope.guild_id is None else "")
        ),
        "활성화 설정 상속 (on/off)",
        *_chain_details(enabled, default="on", include_server=scope.guild_id is not None),
        "수집 범위 상속 (all/direct)",
        *_chain_details(capture, default="all", include_server=scope.guild_id is not None),
    ]
    if external_context_policy is not None:
        # This is the egress boundary for *recent channel rows*. Other context
        # types and request-time filters have their own separate rules.
        if actual_recent == "off":
            egress = "없음 (채널 recent buffer 미사용)"
        elif strict_policy(external_context_policy):
            egress = "직접 호출 발언·히나 답변 등 허용된 항목만 (일반 잡담 제외)"
        else:
            egress = "로컬 문맥 중 다른 전송 경계에서도 허용된 항목"
        lines.extend([
            "",
            "외부 LLM 전송 (최근 채널 문맥 기준)",
            f"프라이버시 정책: **{external_context_policy}**",
            f"전송 가능 범위: {egress}",
        ])
    lines.extend([
        "",
        "수동 메모 (본문 비공개)",
        f"개인 메모: {'있음' if user_note else '없음'}",
    ]
    if scope.guild_id is not None:
        lines.append(f"서버 공통 메모: {'있음' if server_note else '없음'}")
    return "\n".join(lines)


@app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
@app_commands.allowed_installs(guilds=True, users=True)
class StateCommands(app_commands.Group):
    def __init__(self, client):
        super().__init__(name="state", description="현재 컨텍스트 상태 조회 (봇 관리자 전용)")
        self.client = client

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id not in self.client.emoji_admin_ids:
            await interaction.response.send_message(
                "봇 소유자 또는 지정된 관리자만 사용할 수 있어요.", ephemeral=True
            )
            return False
        return True

    async def on_error(self, interaction, error):
        log.warning("State command failed (%s)", type(error).__name__)
        message = "상태를 확인하지 못했어요. 잠시 후 다시 시도해 주세요."
        if interaction.response.is_done():
            await interaction.followup.send(message, ephemeral=True)
        else:
            await interaction.response.send_message(message, ephemeral=True)

    @app_commands.command(name="show", description="현재 채널의 기억·최근 문맥 설정 및 메모 존재 상태")
    @app_commands.describe(
        channel="같은 서버의 다른 채널 (생략하면 현재 채널)",
        user="현재 서버의 멤버 (생략하면 호출자; 설정 모드는 사용자별로 다르지 않음)",
    )
    async def show(
        self,
        interaction: discord.Interaction,
        channel: discord.TextChannel | discord.Thread | None = None,
        user: discord.Member | None = None,
    ):
        try:
            scope = command_target_scope(interaction, channel=channel)
            if user is not None:
                if scope.guild_id is None:
                    raise ValueError("DM에서는 다른 사용자 상태를 조회할 수 없어요.")
                if getattr(getattr(user, "guild", None), "id", None) != scope.guild_id:
                    raise ValueError("현재 서버의 멤버만 지정할 수 있어요.")
                scope = Scope(scope.guild_id, scope.channel_id, user.id)
            settings = getattr(self.client, "settings", None)
            output = effective_state_text(
                self.client.store,
                scope,
                external_context_policy=getattr(settings, "external_context_policy", None),
            )
        except ValueError as exc:
            await interaction.response.send_message(str(exc), ephemeral=True)
            return
        await interaction.response.send_message(output, ephemeral=True)

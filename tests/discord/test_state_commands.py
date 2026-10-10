"""Stage 3: /state show policy chains, note privacy, and scope permissions."""

from types import SimpleNamespace as NS
from unittest.mock import AsyncMock

import pytest

from hina_bot.core.routing import Scope
from hina_bot.core.scope_overrides import (
    effective_recent_context_mode,
    memory_mode_capabilities,
    resolve_scope_chain,
)
from hina_bot.core.store import Store
from hina_bot.discord.chatlog_capture import (
    capture_mode_overrides,
    set_capture_mode_override,
)
from hina_bot.discord.chatlog_commands import _set_mode_override
from hina_bot.discord.state_commands import StateCommands, effective_state_text


def _channel(channel_id, *, guild_id=1, visible=True):
    return NS(
        id=channel_id,
        guild=NS(id=guild_id),
        permissions_for=lambda _user: NS(view_channel=visible),
    )


def _interaction(*, guild_id=1, channel_id=10, user_id=100):
    return NS(
        guild_id=guild_id,
        channel_id=channel_id,
        user=NS(id=user_id),
        response=NS(send_message=AsyncMock(), is_done=lambda: False),
    )


@pytest.fixture
def state_context():
    store = Store(":memory:")
    # Store initialization performs the legacy chatlog migration independently of commands.
    client = NS(
        store=store,
        emoji_admin_ids={100},
        settings=NS(external_context_policy="full"),
    )
    yield store, StateCommands(client)
    store.close()


@pytest.mark.asyncio
async def test_state_command_is_admin_only_and_ephemeral(state_context):
    _, group = state_context
    denied = _interaction(user_id=200)
    assert not await group.interaction_check(denied)
    assert denied.response.send_message.call_args.kwargs["ephemeral"] is True
    admin = _interaction()
    assert await group.interaction_check(admin)
    await group.show.callback(group, admin)
    assert admin.response.send_message.call_args.kwargs["ephemeral"] is True
    assert "자동 장기 기억" in admin.response.send_message.call_args.args[0]
    assert "최근 채널 대화 문맥" in admin.response.send_message.call_args.args[0]


@pytest.mark.asyncio
async def test_state_shows_effective_precedence_and_memory_capabilities(state_context):
    store, group = state_context
    store.set_memory_mode_override("global", "off")
    store.set_memory_mode_override("guild:1", "read_only")
    store.set_memory_mode_override("guild:1:channel:20", "write_only")
    _set_mode_override(store, "global", "direct")
    _set_mode_override(store, "guild:1", "all")
    _set_mode_override(store, "guild:1:channel:20", "off")

    interaction = _interaction()
    await group.show.callback(group, interaction, _channel(20))
    result = interaction.response.send_message.call_args.args[0]

    assert "<#20>" in result
    assert "최종 적용: **write_only** (출처: 채널)" in result
    assert "읽기: 꺼짐 / 쓰기: 켜짐" in result
    assert "전역: `off`" in result
    assert "서버: `read_only`" in result
    assert "채널: `write_only`" in result
    assert "활성화 설정: **off** (출처: 채널)" in result
    assert "수집 범위 설정: **all** (출처: 채널)" in result
    assert "로컬 최근 문맥: **off**" in result
    assert "활성화 설정 상속" in result
    assert "수집 범위 상속" in result

    # Same underlying override chains and capability logic as Dashboard /state.
    scope = Scope(1, 20, 100)
    from_dashboard_memory = resolve_scope_chain(
        store.memory_mode_overrides(), scope, default="normal"
    )
    from_dashboard_enabled = resolve_scope_chain(
        store.chat_log_mode_overrides(), scope, default="on"
    )
    from_dashboard_capture = resolve_scope_chain(
        capture_mode_overrides(store), scope, default="all"
    )
    assert from_dashboard_memory["effective"] == "write_only"
    assert memory_mode_capabilities(from_dashboard_memory["effective"]) == (False, True)
    assert effective_recent_context_mode(
        scope,
        chat_log_mode=from_dashboard_enabled["effective"],
        capture_mode=from_dashboard_capture["effective"],
    ) == "off"


@pytest.mark.asyncio
async def test_inheritance_and_default_source_are_reported(state_context):
    store, group = state_context
    store.set_memory_mode_override("global", "read_only")
    _set_mode_override(store, "global", "direct")
    interaction = _interaction()
    await group.show.callback(group, interaction)
    result = interaction.response.send_message.call_args.args[0]
    assert "최종 적용: **read_only** (출처: 전역)" in result
    assert "활성화 설정: **on** (출처: 전역)" in result
    assert "수집 범위 설정: **direct** (출처: 전역)" in result
    assert "로컬 최근 문맥: **direct**" in result
    assert "상속 → read_only" in result
    assert "상속 → direct" in result

    store.set_memory_mode_override("global", None)
    _set_mode_override(store, "global", None)
    interaction = _interaction()
    await group.show.callback(group, interaction)
    result = interaction.response.send_message.call_args.args[0]
    assert "최종 적용: **normal** (출처: 기본값)" in result
    assert "활성화 설정: **on** (출처: 기본값)" in result
    assert "수집 범위 설정: **all** (출처: 기본값)" in result


@pytest.mark.asyncio
async def test_manual_note_contents_are_never_exposed(state_context):
    store, group = state_context
    scope = Scope(1, 10, 100)
    store.set_note(scope.user_note, "super secret user note")
    store.set_note(scope.realm, "private server instruction")
    interaction = _interaction()
    await group.show.callback(group, interaction)
    result = interaction.response.send_message.call_args.args[0]
    assert "개인 메모: 있음" in result
    assert "서버 공통 메모: 있음" in result
    assert "super secret user note" not in result
    assert "private server instruction" not in result


@pytest.mark.asyncio
async def test_other_user_selection_changes_note_presence_not_channel_policy(state_context):
    store, group = state_context
    store.set_note(Scope(1, 10, 200).user_note, "a confidential user note")
    store.set_memory_mode_override("guild:1", "off")
    interaction = _interaction()
    selected_member = NS(id=200, guild=NS(id=1))
    await group.show.callback(group, interaction, None, selected_member)
    result = interaction.response.send_message.call_args.args[0]
    assert "사용자 ID: `200`" in result
    assert "개인 메모: 있음" in result
    assert "최종 적용: **off** (출처: 서버)" in result
    assert "confidential" not in result


@pytest.mark.parametrize("channel,user,error", [
    (_channel(20, guild_id=2), None, "현재 서버"),
    (_channel(20, visible=False), None, "조회 권한"),
    (None, NS(id=200, guild=NS(id=2)), "현재 서버"),
])
@pytest.mark.asyncio
async def test_invalid_scopes_do_not_reveal_any_state(state_context, channel, user, error):
    store, group = state_context
    store.set_note("guild:1:user:200", "secret")
    interaction = _interaction()
    await group.show.callback(group, interaction, channel, user)
    reply = interaction.response.send_message.call_args.args[0]
    assert error in reply
    assert "secret" not in reply
    assert "자동 장기 기억" not in reply


@pytest.mark.asyncio
async def test_dm_recent_context_is_effectively_off_and_other_user_is_forbidden(state_context):
    store, group = state_context
    _set_mode_override(store, "global", "all")
    interaction = _interaction(guild_id=None, channel_id=99)
    await group.show.callback(group, interaction)
    result = interaction.response.send_message.call_args.args[0]
    assert "활성화 설정: **on**" in result
    assert "수집 범위 설정: **all**" in result
    assert "로컬 최근 문맥: **off** (DM에서는 사용하지 않음)" in result
    assert "전송 가능 범위: 없음 (채널 recent buffer 미사용)" in result
    assert "서버 공통 메모" not in result

    await group.show.callback(group, interaction, None, NS(id=200, guild=NS(id=1)))
    assert "DM에서는 다른 사용자" in interaction.response.send_message.call_args.args[0]


def test_pure_render_matches_current_store_effective_policy(state_context):
    store, _ = state_context
    scope = Scope(1, 55, 100)
    store.set_memory_mode_override(scope.channel, "normal")
    _set_mode_override(store, scope.channel, "direct")
    result = effective_state_text(store, scope)
    assert "최종 적용: **normal** (출처: 채널)" in result
    assert "활성화 설정: **on** (출처: 채널)" in result
    assert "수집 범위 설정: **direct** (출처: 채널)" in result
    assert "로컬 최근 문맥: **direct**" in result


@pytest.mark.parametrize(
    "on_off,capture,expected",
    [
        ("off", "direct", "off"),
        ("on", "direct", "direct"),
        ("off", "all", "off"),
        ("on", "all", "all"),
    ],
)
def test_mixed_legacy_overrides_match_dashboard_and_runtime(
    state_context, on_off, capture, expected,
):
    store, _ = state_context
    scope = Scope(1, 10, 100)
    # Legacy/partial overrides can have different sources for enable and capture.
    store.set_chat_log_mode_override("global", on_off)
    set_capture_mode_override(store, scope.channel, capture)
    text = effective_state_text(store, scope, external_context_policy="full")
    enabled = resolve_scope_chain(store.chat_log_mode_overrides(), scope, default="on")
    captured = resolve_scope_chain(capture_mode_overrides(store), scope, default="all")
    dashboard_effective = effective_recent_context_mode(
        scope,
        chat_log_mode=enabled["effective"],
        capture_mode=captured["effective"],
    )
    assert dashboard_effective == expected
    assert f"로컬 최근 문맥: **{dashboard_effective}**" in text
    assert f"활성화 설정: **{on_off}** (출처: 전역)" in text
    assert f"수집 범위 설정: **{capture}** (출처: 채널)" in text
    assert "전송 가능 범위: 없음" in text if expected == "off" else (
        "전송 가능 범위: 로컬 문맥 중 다른 전송 경계에서도 허용된 항목" in text
    )


@pytest.mark.asyncio
async def test_external_egress_policy_is_separate_from_local_capture(state_context):
    store, group = state_context
    _set_mode_override(store, "global", "all")
    interaction = _interaction()

    # The local all mode must never be presented as unrestricted external egress.
    group.client.settings.external_context_policy = "bot_interactions_only"
    await group.show.callback(group, interaction)
    text = interaction.response.send_message.call_args.args[0]
    assert "로컬 최근 문맥: **all**" in text
    assert "프라이버시 정책: **bot_interactions_only**" in text
    assert "전송 가능 범위: 직접 호출 발언·히나 답변 등 허용된 항목만" in text
    assert "일반 잡담 제외" in text

    group.client.settings.external_context_policy = "full"
    await group.show.callback(group, interaction)
    text = interaction.response.send_message.call_args.args[0]
    assert "로컬 최근 문맥: **all**" in text
    assert "프라이버시 정책: **full**" in text
    assert "전송 가능 범위: 로컬 문맥 중 다른 전송 경계에서도 허용된 항목" in text


@pytest.mark.asyncio
async def test_egress_boundary_is_explicitly_not_channel_recent_when_off(state_context):
    store, group = state_context
    _set_mode_override(store, "global", "off")
    group.client.settings.external_context_policy = "bot_interactions_only"
    await group.show.callback(group, _interaction())
    text = group.client  # Verify via the pure formatter as well.
    rendered = effective_state_text(
        store, Scope(1, 10, 100), external_context_policy="bot_interactions_only"
    )
    assert "로컬 최근 문맥: **off**" in rendered
    assert "전송 가능 범위: 없음 (채널 recent buffer 미사용)" in rendered

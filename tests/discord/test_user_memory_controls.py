"""Owner-scoped Discord memory controls and permission intersections."""

from types import SimpleNamespace as NS
from unittest.mock import AsyncMock

import pytest

from hina_bot.core.config import Settings
from hina_bot.core.routing import Scope
from hina_bot.core.store import Store
from hina_bot.discord.web_bot import HinaClient


@pytest.fixture
def bot():
    store = Store.create(":memory:")
    client = HinaClient(
        Settings(discord_token="test", openai_api_key="test", bot_admin_ids=frozenset({100})),
        store=store,
        llm=NS(close=AsyncMock()),
    )
    yield client
    store.close()


def _interaction(user_id=200, *, guild_id=1, channel_id=10):
    return NS(
        guild_id=guild_id,
        channel_id=channel_id,
        user=NS(id=user_id),
        response=NS(
            send_message=AsyncMock(), defer=AsyncMock(),
            is_done=lambda: False,
        ),
        followup=NS(send=AsyncMock()),
    )


def _memory(store, scope, text, message_id):
    store.add(scope, message_id, "turn", "reply")
    return store.add_memory_item(
        scope, text, kind="fact", disclosure="local",
        source_message_ids=(str(message_id),),
    )


@pytest.mark.asyncio
async def test_preference_applies_to_same_owner_across_spaces_and_preserves_other_users(bot):
    command = bot.tree.get_command("memory").get_command("preference")
    interaction = _interaction()
    scope = Scope(1, 10, 200)
    dm = Scope(None, 30, 200)
    other = Scope(1, 10, 300)

    await command.callback(interaction, "off")
    assert bot.store.user_memory_mode(200) == "off"
    assert bot.store.memory_mode(scope) == "off"
    assert bot.store.memory_mode(dm) == "off"
    assert bot.store.memory_mode(other) == "normal"
    assert interaction.response.send_message.call_args.kwargs["ephemeral"]

    await command.callback(interaction, "read_only")
    assert bot.store.memory_mode(scope) == "read_only"
    bot.store.set_memory_mode_override(scope.channel, "write_only")
    assert bot.store.memory_mode(scope) == "off"  # No policy can grant the other permission.
    assert bot.store.memory_mode(other) == "write_only"
    await command.callback(interaction, "normal")
    assert bot.store.memory_mode(scope) == "write_only"


@pytest.mark.asyncio
async def test_show_all_pages_and_never_includes_another_owner(bot):
    store = bot.store
    local = Scope(1, 10, 200)
    other_guild = Scope(2, 20, 200)
    stranger = Scope(1, 10, 300)
    first_id = _memory(store, local, "first-owner-local", 101)
    cross_id = _memory(store, other_guild, "second-owner-cross-guild", 102)
    stranger_id = _memory(store, stranger, "other-owner-secret", 103)

    show = bot.tree.get_command("memory").get_command("show")
    interaction = _interaction()
    await show.callback(interaction, "all", 1)
    shown = interaction.response.send_message.call_args.args[0]
    assert f"#{first_id}" in shown
    assert f"#{cross_id}" in shown
    assert "other-owner-secret" not in shown
    assert f"#{stranger_id}" not in shown
    assert interaction.response.send_message.call_args.kwargs["ephemeral"]

    interaction = _interaction()
    await show.callback(interaction, "current", 1)
    shown = interaction.response.send_message.call_args.args[0]
    assert f"#{first_id}" in shown
    assert f"#{cross_id}" not in shown

    interaction = _interaction()
    await show.callback(interaction, "all", 2)
    assert "1~1" in interaction.response.send_message.call_args.args[0]


@pytest.mark.asyncio
async def test_remove_requires_owner_confirmation_and_preserves_retraction_audit(bot):
    store = bot.store
    own = Scope(1, 10, 200)
    other = Scope(2, 20, 300)
    own_id = _memory(store, own, "wrong-memory", 201)
    other_id = _memory(store, other, "other-user", 202)
    remove = bot.tree.get_command("memory").get_command("remove")

    interaction = _interaction()
    await remove.callback(interaction, other_id, True)
    assert "본인의 기억" in interaction.response.send_message.call_args.args[0]
    assert store.memory_item(other_id).status.value == "active"

    interaction = _interaction()
    await remove.callback(interaction, own_id)
    assert store.memory_item(own_id).status.value == "active"
    assert "confirm" in interaction.response.send_message.call_args.args[0]

    interaction = _interaction()
    await remove.callback(interaction, own_id, True)
    assert store.memory_item(own_id).status.value == "retracted"
    assert store.memory_items(200) == []
    assert store.memory_item(own_id).content == "wrong-memory"
    assert interaction.followup.send.call_args.kwargs["ephemeral"]


@pytest.mark.asyncio
async def test_status_reports_admin_and_personal_intersection(bot):
    bot.store.set_user_memory_mode(200, "read_only")
    bot.store.set_memory_mode_override("guild:1", "write_only")
    status = bot.tree.get_command("memory").get_command("status")
    interaction = _interaction()
    await status.callback(interaction)
    message = interaction.response.send_message.call_args.args[0]
    assert "read_only" in message
    assert "write_only" in message
    assert "최종: **off**" in message
    assert "새 장기기억 저장: 꺼짐" in message
    assert interaction.response.send_message.call_args.kwargs["ephemeral"]


def test_user_preference_persists_without_schema_changes(tmp_path):
    path = tmp_path / "memory.sqlite3"
    store = Store.create(str(path))
    store.set_user_memory_mode(200, "off")
    store.close()

    reopened = Store.open_existing(str(path))
    try:
        assert reopened.user_memory_mode(200) == "off"
        assert reopened.user_memory_mode(300) == "normal"
        assert reopened.memory_mode(Scope(1, 10, 200)) == "off"
        assert "user:200" in reopened.memory_mode_overrides()
        with pytest.raises(ValueError):
            reopened.set_user_memory_mode(200, "write_only")
    finally:
        reopened.close()

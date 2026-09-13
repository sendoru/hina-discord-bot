from types import SimpleNamespace as NS
from unittest.mock import AsyncMock

import pytest

from hina_bot.config import Settings
from hina_bot.routing import Scope
from hina_bot.slash_commands import EmojiSlashCommands, _parse_emoji_import_items
from hina_bot.store import Store
from hina_bot.web_bot import HinaClient


@pytest.fixture
def slash_bot():
    store = Store(":memory:")
    llm = NS(close=AsyncMock())
    bot = HinaClient(
        Settings("test", "test", cooldown=0, bot_admin_ids=frozenset({100})),
        store=store,
        llm=llm,
    )
    yield bot
    store.close()


def test_runtime_registers_separated_memory_and_chatlog_commands(slash_bot):
    memory = slash_bot.tree.get_command("memory")
    assert memory is not None
    for name in (
        "mode", "status", "overview", "purge", "show", "note", "note-clear", "clear",
        "server-show", "server-note", "server-clear",
    ):
        assert memory.get_command(name) is not None
    assert memory.get_command("chatlog") is None

    chatlog = slash_bot.tree.get_command("chatlog")
    assert chatlog is not None
    for name in ("mode", "status", "overview", "clear"):
        assert chatlog.get_command(name) is not None

    emoji = slash_bot.tree.get_command("emoji")
    assert emoji is not None
    for name in ("add", "import", "list", "edit", "remove"):
        assert emoji.get_command(name) is not None

    assert slash_bot.tree.get_command("instruction") is not None
    assert slash_bot.tree.get_command("knowledge") is not None
    assert slash_bot.tree.get_command("help") is not None


def test_emoji_import_items_parser():
    assert _parse_emoji_import_items(
        "hina_sleep | 졸릴 때\n\nhina_cry | 슬프거나 울고 싶을 때"
    ) == [
        ("hina_sleep", "졸릴 때"),
        ("hina_cry", "슬프거나 울고 싶을 때"),
    ]
    with pytest.raises(ValueError, match="형식"):
        _parse_emoji_import_items("hina_sleep 졸릴 때")
    with pytest.raises(ValueError, match="중복"):
        _parse_emoji_import_items("hina_sleep | 졸릴 때\nhina_sleep | 잘 때")


@pytest.mark.asyncio
async def test_emoji_bulk_import_matches_current_guild_emoji_names():
    registry = NS(add=AsyncMock(side_effect=["<:hina_sleep:1>", "<:hina_cry:2>"]))
    group = EmojiSlashCommands(NS(emoji_admin_ids={100}, emoji_registry=registry))
    command = group.get_command("import")
    guild = NS(emojis=[
        NS(name="hina_sleep", id=1, is_usable=lambda: True),
        NS(name="hina_cry", id=2, is_usable=lambda: True),
    ])
    response = NS(
        defer=AsyncMock(),
        send_message=AsyncMock(),
        is_done=lambda: True,
    )
    followup = NS(send=AsyncMock())
    interaction = NS(guild=guild, user=NS(id=100), response=response, followup=followup)

    await command.callback(
        group,
        interaction,
        "hina_sleep | 졸리거나 잠이 올 때\nhina_cry | 슬프거나 울고 싶을 때",
    )

    assert registry.add.await_count == 2
    assert registry.add.await_args_list[0].args[:2] == ("hina_sleep", "졸리거나 잠이 올 때")
    assert registry.add.await_args_list[0].kwargs == {"source": "1"}
    assert registry.add.await_args_list[1].kwargs == {"source": "2"}
    response.defer.assert_awaited_once()
    text = followup.send.await_args_list[0].args[0]
    assert "2/2개 등록 완료" in text
    assert "hina_sleep" in text
    assert "hina_cry" in text


@pytest.mark.asyncio
async def test_emoji_bulk_import_reports_missing_source_without_aborting_batch():
    registry = NS(add=AsyncMock(return_value="<:hina_sleep:1>"))
    group = EmojiSlashCommands(NS(emoji_admin_ids={100}, emoji_registry=registry))
    command = group.get_command("import")
    guild = NS(emojis=[NS(name="hina_sleep", id=1, is_usable=lambda: True)])
    response = NS(defer=AsyncMock(), send_message=AsyncMock(), is_done=lambda: True)
    followup = NS(send=AsyncMock())
    interaction = NS(guild=guild, user=NS(id=100), response=response, followup=followup)

    await command.callback(
        group,
        interaction,
        "hina_sleep | 졸릴 때\nhina_missing | 테스트",
    )

    registry.add.assert_awaited_once()
    text = followup.send.await_args_list[0].args[0]
    assert "1/2개 등록 완료" in text
    assert "hina_missing" in text
    assert "찾지 못했어요" in text


@pytest.mark.asyncio
async def test_production_client_disables_prefix_slash_parser(slash_bot):
    assert slash_bot._management_text("/메모 old-style") is False
    assert await slash_bot.command(None, None, "/메모 old-style") is None


@pytest.mark.asyncio
async def test_memory_group_keeps_only_admin_memory_commands_admin_only(slash_bot):
    memory = slash_bot.tree.get_command("memory")

    user_response = NS(send_message=AsyncMock())
    user = NS(id=200)
    show_interaction = NS(user=user, command=NS(name="show"), response=user_response)
    assert await memory.interaction_check(show_interaction) is True

    for name in ("mode", "status", "overview", "purge"):
        interaction = NS(user=user, command=NS(name=name), response=user_response)
        assert await memory.interaction_check(interaction) is False
    user_response.send_message.assert_awaited()

    admin_interaction = NS(
        user=NS(id=100),
        command=NS(name="purge"),
        response=NS(send_message=AsyncMock()),
    )
    assert await memory.interaction_check(admin_interaction) is True


@pytest.mark.asyncio
async def test_chatlog_group_is_bot_admin_only(slash_bot):
    chatlog = slash_bot.tree.get_command("chatlog")
    response = NS(send_message=AsyncMock())
    assert await chatlog.interaction_check(NS(user=NS(id=200), response=response)) is False
    assert await chatlog.interaction_check(
        NS(user=NS(id=100), response=NS(send_message=AsyncMock()))) is True


@pytest.mark.asyncio
async def test_memory_note_is_available_as_slash_command(slash_bot):
    memory = slash_bot.tree.get_command("memory")
    note = memory.get_command("note")
    response = NS(send_message=AsyncMock())
    interaction = NS(
        guild_id=1,
        channel_id=10,
        user=NS(id=200, guild_permissions=NS(manage_guild=False)),
        response=response,
    )

    await note.callback(interaction, "새 개인 메모")

    assert slash_bot.store.note("guild:1:user:200") == "새 개인 메모"
    response.send_message.assert_awaited()


@pytest.mark.asyncio
async def test_memory_clear_does_not_require_guild_admin_or_clear_chatlog(slash_bot):
    memory = slash_bot.tree.get_command("memory")
    clear = memory.get_command("clear")
    scope = Scope(1, 10, 200)
    slash_bot.store.add(scope, 1, "remember", "reply")
    slash_bot.store.set_note(scope.user_note, "note")
    slash_bot.recent.add(scope, 2, "user", "recent")
    response = NS(send_message=AsyncMock())
    interaction = NS(
        guild_id=1,
        channel_id=10,
        user=NS(id=200, guild_permissions=NS(manage_guild=False)),
        response=response,
    )

    await clear.callback(interaction, True)

    assert slash_bot.store.history(scope) == []
    assert slash_bot.store.note(scope.user_note) == ""
    assert len(slash_bot.recent.context(scope, 999)) == 1
    text = response.send_message.call_args.args[0]
    assert "최근 채널 대화 문맥은 그대로" in text
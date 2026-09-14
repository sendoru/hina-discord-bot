from datetime import UTC, datetime, timedelta
from types import SimpleNamespace as NS

import pytest

from hina_bot.ai.egress_policy import BOT_INTERACTIONS_ONLY, filter_channel_context
from hina_bot.core.routing import Scope
from hina_bot.core.store import Store
from hina_bot.discord.chatlog_capture import set_capture_mode_override
from hina_bot.discord.target_context import TARGET_CONTEXT, collect, retrieval_mode
from hina_bot.discord.target_recent import TargetAwareRecentMessages
from hina_bot.discord.web_bot import _target_history_visibility


class FakeHistoryChannel:
    def __init__(self, messages=()):
        self.messages = list(messages)
        self.id = 10
        self.calls = []

    def history(self, **kwargs):
        self.calls.append(kwargs)

        async def rows():
            for message in self.messages:
                yield message

        return rows()


def old(message_id, content, author, created_at, *, mentions=()):
    return NS(
        id=message_id,
        content=content,
        author=author,
        webhook_id=None,
        created_at=created_at,
        mentions=list(mentions),
        guild=NS(id=1),
    )


def request(content, target, channel, created_at):
    return NS(
        id=100,
        content=content,
        author=NS(id=1000, bot=False, display_name="요청자", name="요청자"),
        guild=NS(id=1),
        channel=channel,
        mentions=[target],
        created_at=created_at,
    )


def test_retrieval_depth_is_chosen_per_request_and_bare_mention_is_none():
    assert retrieval_mode("<@200> 아까 뭐라고 했어?") == "basic"
    assert retrieval_mode("<@200>의 채팅 기록을 분석해 줘") == "deep"
    assert retrieval_mode("<@200>은 어떤 사람 같아?") == "deep"
    assert retrieval_mode("<@200> 안녕") is None


@pytest.mark.asyncio
async def test_bare_mention_and_off_mode_do_not_read_discord_history():
    now = datetime.now(UTC)
    target = NS(id=200, bot=False, display_name="대상", name="대상")
    channel = FakeHistoryChannel()

    assert await collect(request("히나야 <@200> 안녕", target, channel, now), 99,
                         "<@200> 안녕") == []
    assert await collect(request("히나야 <@200>의 채팅 기록", target, channel, now), 99,
                         "<@200>의 채팅 기록을 읽어 줘", visibility_mode="off") == []
    assert channel.calls == []


@pytest.mark.asyncio
async def test_basic_lookup_has_small_request_scoped_limits():
    now = datetime.now(UTC)
    target = NS(id=200, bot=False, display_name="대상", name="대상")
    messages = [old(i, f"발언 {i}", target, now - timedelta(minutes=i)) for i in range(1, 6)]
    channel = FakeHistoryChannel(messages)

    rows = await collect(
        request("히나야 <@200> 아까 뭐라고 했어?", target, channel, now),
        99,
        "<@200> 아까 뭐라고 했어?",
    )

    assert rows[0]["retrieval_mode"] == "basic"
    assert [row["content"] for row in rows[0]["sampled_messages"]] == [
        "발언 3", "발언 2", "발언 1",
    ]
    assert channel.calls[0]["limit"] == 120
    assert now - channel.calls[0]["after"] == timedelta(days=1)


@pytest.mark.asyncio
async def test_deep_direct_lookup_keeps_only_verified_bot_calls():
    now = datetime.now(UTC)
    target = NS(id=200, bot=False, display_name="대상", name="대상")
    bot = NS(id=99, bot=True, display_name="히나", name="히나")
    ordinary = old(1, "그냥 채널에서 한 말", target, now - timedelta(minutes=2))
    direct = old(2, "히나야 직접 한 말", target, now - timedelta(minutes=1), mentions=[bot])
    channel = FakeHistoryChannel([direct, ordinary])

    rows = await collect(
        request("히나야 <@200>의 채팅 기록을 분석해 줘", target, channel, now),
        99,
        "<@200>의 채팅 기록을 분석해 줘",
        visibility_mode="direct",
    )

    assert rows[0]["retrieval_mode"] == "deep"
    assert rows[0]["explicit_history_request"] is True
    assert rows[0]["sampled_messages"] == [{
        "message_id": "2",
        "at": direct.created_at.isoformat(),
        "content": "히나야 직접 한 말",
        "direct_trigger": True,
    }]
    assert channel.calls[0]["limit"] == 300
    assert now - channel.calls[0]["after"] == timedelta(days=7)


@pytest.mark.asyncio
async def test_full_all_lookup_marks_ambient_and_direct_provenance():
    now = datetime.now(UTC)
    target = NS(id=200, bot=False, display_name="대상", name="대상")
    bot = NS(id=99, bot=True, display_name="히나", name="히나")
    ordinary = old(1, "일반 발언", target, now - timedelta(minutes=2))
    direct = old(2, "히나야 직접 발언", target, now - timedelta(minutes=1), mentions=[bot])
    channel = FakeHistoryChannel([direct, ordinary])

    rows = await collect(
        request("히나야 <@200>은 어떤 사람 같아?", target, channel, now),
        99,
        "<@200>은 어떤 사람 같아?",
        visibility_mode="all",
    )

    assert [row["direct_trigger"] for row in rows[0]["sampled_messages"]] == [False, True]


def test_visibility_combines_chatlog_and_external_policy():
    store = Store(":memory:")
    scope = Scope(1, 10, 100)
    try:
        assert _target_history_visibility(store, scope, strict_egress=False) == "all"
        set_capture_mode_override(store, "global", "direct")
        assert _target_history_visibility(store, scope, strict_egress=False) == "direct"
        set_capture_mode_override(store, "global", "all")
        assert _target_history_visibility(store, scope, strict_egress=True) == "direct"
        store.set_chat_log_mode_override("global", "off")
        assert _target_history_visibility(store, scope, strict_egress=False) == "off"
    finally:
        store.close()


def test_deep_rows_reach_context_and_strict_egress_keeps_only_direct_calls():
    recent = TargetAwareRecentMessages(budget=6000)
    scope = Scope(1, 10, 100)
    sampled = [
        {"message_id": str(i), "content": f"message {i}", "at": "",
         "direct_trigger": i % 2 == 0}
        for i in range(1, 7)
    ]
    token = TARGET_CONTEXT.set(({
        "user_id": "200",
        "name": "대상",
        "retrieval_mode": "deep",
        "explicit_history_request": True,
        "sampled_messages": sampled,
    },))
    try:
        rows = recent.context(scope, 100)
    finally:
        TARGET_CONTEXT.reset(token)

    targets = [row for row in rows if row.get("context_kind") == "target_user_history"]
    assert len(targets) == 6
    assert all(row["target_retrieval_mode"] == "deep" for row in targets)
    strict = filter_channel_context(targets, 100, BOT_INTERACTIONS_ONLY)
    assert [row["message_id"] for row in strict] == ["2", "4", "6"]


def test_fresh_target_sample_replaces_duplicate_passive_recent_row():
    recent = TargetAwareRecentMessages(budget=6000)
    scope = Scope(1, 10, 100)
    recent.add(
        Scope(1, 10, 200), 2, "대상", "히나야 직접 발언",
        direct_trigger=True,
    )
    token = TARGET_CONTEXT.set(({
        "user_id": "200",
        "name": "대상",
        "retrieval_mode": "deep",
        "sampled_messages": [{
            "message_id": "2",
            "content": "히나야 직접 발언",
            "at": "",
            "direct_trigger": True,
        }],
    },))
    try:
        rows = recent.context(scope, 100)
    finally:
        TARGET_CONTEXT.reset(token)

    assert len([row for row in rows if row["message_id"] == "2"]) == 1
    target = next(row for row in rows if row["message_id"] == "2")
    assert target["context_kind"] == "target_user_history"
    assert target["target_retrieval_mode"] == "deep"

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace as NS

import pytest

from hina_bot.discord.target_context import collect


class FakeHistoryChannel:
    def __init__(self, messages):
        self.messages = list(messages)
        self.id = 10

    def history(self, **kwargs):
        async def rows():
            for message in self.messages:
                yield message

        return rows()


def _old(message_id, content, author, created_at, *, mentions=()):
    return NS(
        id=message_id,
        content=content,
        author=author,
        webhook_id=None,
        created_at=created_at,
        mentions=list(mentions),
        guild=NS(id=1),
    )


def _request(content, target, channel, created_at):
    return NS(
        id=100,
        content=content,
        author=NS(id=1000, bot=False, display_name="요청자", name="요청자"),
        guild=NS(id=1),
        channel=channel,
        mentions=[target],
        created_at=created_at,
    )


@pytest.mark.asyncio
async def test_mention_gets_basic_history_even_without_profile_keywords():
    now = datetime.now(UTC)
    target = NS(id=200, bot=False, display_name="대상", name="대상")
    messages = [
        _old(i, f"일반 발언 {i}", target, now - timedelta(minutes=i))
        for i in range(1, 6)
    ]
    channel = FakeHistoryChannel(messages)
    message = _request("히나야 <@200> 얘 요즘 왜 이래?", target, channel, now)

    sampled = await collect(message, 99, "<@200> 얘 요즘 왜 이래?")

    assert len(sampled) == 1
    assert sampled[0]["retrieval_mode"] == "basic"
    assert sampled[0]["explicit_history_request"] is False
    assert [row["content"] for row in sampled[0]["sampled_messages"]] == [
        "일반 발언 3",
        "일반 발언 2",
        "일반 발언 1",
    ]


@pytest.mark.asyncio
async def test_explicit_history_request_reads_visible_side_chat_in_direct_mode():
    now = datetime.now(UTC)
    target = NS(id=200, bot=False, display_name="대상", name="대상")
    bot = NS(id=99, bot=True, display_name="히나", name="히나")
    ordinary = _old(1, "그냥 채널에서 한 말", target, now - timedelta(minutes=2))
    direct = _old(
        2,
        "히나야 직접 한 말",
        target,
        now - timedelta(minutes=1),
        mentions=[bot],
    )
    channel = FakeHistoryChannel([direct, ordinary])
    message = _request("히나야 <@200>의 채팅 기록을 읽어봐", target, channel, now)

    sampled = await collect(
        message,
        99,
        "<@200>의 채팅 기록을 읽어봐",
        direct_only=True,
        call_prefixes=("히나야",),
    )

    assert sampled[0]["retrieval_mode"] == "deep"
    assert sampled[0]["explicit_history_request"] is True
    assert [row["content"] for row in sampled[0]["sampled_messages"]] == [
        "그냥 채널에서 한 말",
        "히나야 직접 한 말",
    ]


@pytest.mark.asyncio
async def test_profile_question_keeps_direct_mode_boundary_without_explicit_history_request():
    now = datetime.now(UTC)
    target = NS(id=200, bot=False, display_name="대상", name="대상")
    bot = NS(id=99, bot=True, display_name="히나", name="히나")
    ordinary = _old(1, "그냥 채널에서 한 말", target, now - timedelta(minutes=2))
    direct = _old(
        2,
        "히나야 직접 한 말",
        target,
        now - timedelta(minutes=1),
        mentions=[bot],
    )
    channel = FakeHistoryChannel([direct, ordinary])
    message = _request("히나야 <@200> 어떻게 생각해?", target, channel, now)

    sampled = await collect(
        message,
        99,
        "<@200> 어떻게 생각해?",
        direct_only=True,
        call_prefixes=("히나야",),
    )

    assert sampled[0]["retrieval_mode"] == "deep"
    assert sampled[0]["explicit_history_request"] is False
    assert [row["content"] for row in sampled[0]["sampled_messages"]] == [
        "히나야 직접 한 말",
    ]

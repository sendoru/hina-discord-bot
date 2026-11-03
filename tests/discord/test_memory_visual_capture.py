"""A user's own photo is held for memory extraction after the turn is stored (#472)."""

import json
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest

from hina_bot.ai.vision import CURRENT_VISUAL_INPUTS, VisualInput
from hina_bot.core.config import Settings
from hina_bot.core.pending_visuals import DiskPendingVisualStore, pending_visual_root
from hina_bot.core.routing import Scope
from hina_bot.core.store import Store
from hina_bot.discord.bot import HinaClient

PNG = b"\x89PNG\r\n\x1a\n" + b"photo-bytes"
SCOPE = Scope(1, 10, 100, True)


def visual(message_id="1", **overrides) -> VisualInput:
    values = {
        "data": PNG, "mime_type": "image/png", "source": "attachment", "name": "dog.png",
        "context_kind": "current_message", "reference_strength": "current_message",
        "message_id": str(message_id), "author_user_id": "100",
    }
    values.update(overrides)
    return VisualInput(**values)


class Harness:
    def __init__(self, tmp_path, **settings):
        self.event_path = tmp_path / "events.jsonl"
        self.database = tmp_path / "hina.sqlite3"
        self.store = Store.create(str(self.database))
        self.llm = NS(
            answer=AsyncMock(return_value="귀여운 강아지네"),
            extract_structured_memory=AsyncMock(),
            summarize_shared=AsyncMock(),
            close=AsyncMock(),
        )
        values = {"memory_visual_input": True, "event_log_path": str(self.event_path)}
        values.update(settings)
        self.bot = HinaClient(
            Settings(discord_token="test", openai_api_key="test", cooldown=0, **values),
            store=self.store,
            llm=self.llm,
        )
        self.bot._connection.user = NS(id=99)
        self.channel = MagicMock(spec=discord.TextChannel)
        self.channel.id = 10
        self.channel.send = AsyncMock(return_value=NS(id=1000))
        self.channel.typing.return_value.__aenter__ = AsyncMock(return_value=None)
        self.channel.typing.return_value.__aexit__ = AsyncMock(return_value=None)
        self.channel.permissions_for.return_value = NS(view_channel=True, read_message_history=True)
        self.guild = NS(id=1, default_role=NS(), unavailable=False, me=NS(), emojis=[])
        self.author = NS(id=100, bot=False, display_name="사용자",
                         guild_permissions=NS(manage_guild=False))

    def message(self, text="히나야 우리 애 오늘 미용했어", id=1):
        return NS(id=id, content=text, author=self.author, guild=self.guild,
                  channel=self.channel, mentions=[], webhook_id=None)

    async def send(self, text="히나야 우리 애 오늘 미용했어", id=1, visuals=()):
        token = CURRENT_VISUAL_INPUTS.set(tuple(visuals))
        try:
            await self.bot.on_message(self.message(text, id))
        finally:
            CURRENT_VISUAL_INPUTS.reset(token)

    def events(self, name):
        if not self.event_path.exists():
            return []
        rows = [json.loads(line) for line in self.event_path.read_text().splitlines()]
        return [row for row in rows if row["event"] == name]

    def held(self, message_id="1"):
        return self.store.pending_visuals.get("guild:1", "10", "100", [message_id])


@pytest.fixture
async def harness(tmp_path):
    value = Harness(tmp_path)
    try:
        yield value
    finally:
        await value.bot.close()


@pytest.mark.asyncio
async def test_a_users_own_attachment_is_held_after_the_turn_is_stored(harness):
    await harness.send(visuals=[visual()])

    assert harness.store.history(SCOPE)[-1]["message_id"] == "1"
    (stored,) = harness.held()["1"]
    assert (stored.mime_type, stored.data) == ("image/png", PNG)
    (event,) = harness.events("memory.visual_stored")
    assert (event["images"], event["offered"], event["evicted"]) == (1, 1, 0)
    assert "dog.png" not in harness.event_path.read_text()
    assert harness.events("turn.completed")[0]["status"] == "completed"


@pytest.mark.asyncio
async def test_nothing_is_held_while_the_feature_is_off(tmp_path):
    harness = Harness(tmp_path, memory_visual_input=False)
    try:
        await harness.send(visuals=[visual()])

        assert harness.store.history(SCOPE)
        assert harness.held() == {} and harness.events("memory.visual_stored") == []
        assert not pending_visual_root(harness.database).exists()
    finally:
        await harness.bot.close()


@pytest.mark.asyncio
async def test_a_photo_without_any_words_is_not_held(harness):
    await harness.send(text="히나야", visuals=[visual()])

    assert harness.llm.answer.await_count == 1  # the bare call with a photo is still answered
    assert harness.held() == {}


@pytest.mark.asyncio
async def test_a_store_failure_never_breaks_the_turn_or_leaks_details(harness, monkeypatch):
    def broken(*args, **kwargs):
        raise OSError("denied: /secret/location")

    monkeypatch.setattr(harness.store.pending_visuals, "put", broken)

    await harness.send(visuals=[visual()])

    assert harness.store.history(SCOPE)
    assert harness.events("turn.completed")[0]["status"] == "completed"
    (failure,) = harness.events("memory.visual_store_failed")
    assert failure["error_type"] == "OSError"
    assert "/secret/location" not in harness.event_path.read_text()


@pytest.mark.asyncio
async def test_evictions_are_reported_without_naming_files(tmp_path):
    harness = Harness(tmp_path, memory_visual_pending_max_mb=16)
    try:
        harness.store.pending_visuals.configure(max_bytes=len(PNG) * 2 + 10)
        await harness.send(id=1, visuals=[visual("1")])
        await harness.send(id=2, visuals=[visual("2")])
        await harness.send(id=3, visuals=[visual("3")])

        events = harness.events("memory.visual_stored")
        assert [event["evicted"] for event in events] == [0, 0, 1]
        assert set(harness.store.pending_visuals.get("guild:1", "10", "100", ["1", "2", "3"])) == {
            "2", "3",
        }
    finally:
        await harness.bot.close()


def test_only_the_users_own_message_attachments_qualify(tmp_path):
    from hina_bot.discord.vision import memory_visuals_for_turn

    visuals = [
        visual("1", name="mine.png"),
        visual("1", source="sticker"),
        visual("1", source="emoji"),
        visual("1", author_user_id="200"),                      # someone else's photo
        visual("1", context_kind="replied_message", reference_strength="explicit_reply"),
        visual("1", context_kind="recent_channel_message", reference_strength="passive_recent"),
        visual("2"),                                             # another message
        visual("1", data=PNG + b"2"),
    ]

    held = memory_visuals_for_turn(visuals, user_id=100, message_id=1)

    assert [(v.message_id, v.index, v.data) for v in held] == [
        ("1", 1, PNG), ("1", 2, PNG + b"2"),
    ]
    assert memory_visuals_for_turn([], user_id=100, message_id=1) == []


@pytest.mark.asyncio
async def test_keep_pending_visuals_skips_foreign_images_and_blank_text(harness):
    token = CURRENT_VISUAL_INPUTS.set((visual(author_user_id="200"),))
    try:
        harness.bot._keep_pending_visuals(SCOPE, 1, "우리 애야", "guild")
        harness.bot._keep_pending_visuals(SCOPE, 1, "   ", "guild")
    finally:
        CURRENT_VISUAL_INPUTS.reset(token)

    assert harness.held() == {} and harness.events("memory.visual_stored") == []


# -- sweep and settings -------------------------------------------------------------------


def test_photos_expire_after_the_stale_sweep_has_had_its_chance(tmp_path):
    day = 24 * 60 * 60

    def ttl(**settings):
        bot = NS(settings=Settings(discord_token="t", openai_api_key="k", **settings))
        return HinaClient._pending_visual_ttl_seconds(bot)

    assert ttl() == day
    assert ttl(structured_memory_stale_after_seconds=3 * day) == 3 * day + 2 * 3600
    assert ttl(structured_memory_sweep_interval_seconds=0,
               structured_memory_stale_after_seconds=2 * day) == 2 * day


@pytest.mark.asyncio
async def test_the_sweep_removes_photos_of_turns_no_longer_waiting(harness):
    await harness.send(id=1, visuals=[visual("1")])
    await harness.send(id=2, visuals=[visual("2")])
    first = harness.store.history(SCOPE)[0]
    harness.store.save_memory_extraction_cursor(SCOPE, first["id"])  # turn 1 was extracted

    harness.bot._sweep_pending_visuals()

    assert set(harness.store.pending_visuals.get("guild:1", "10", "100", ["1", "2"])) == {"2"}
    (event,) = harness.events("memory.visual_swept")
    assert event["removed"] == 1


@pytest.mark.asyncio
async def test_a_sweep_failure_is_reported_content_free_and_never_raises(harness, monkeypatch):
    def broken(*args, **kwargs):
        raise OSError("denied: /secret/location")

    monkeypatch.setattr(harness.store.pending_visuals, "sweep", broken)

    harness.bot._sweep_pending_visuals()

    (failure,) = harness.events("memory.visual_sweep_failed")
    assert failure["error_type"] == "OSError"
    assert "/secret/location" not in harness.event_path.read_text()


@pytest.mark.asyncio
async def test_the_configured_disk_limit_reaches_the_store(tmp_path):
    harness = Harness(tmp_path, memory_visual_pending_max_mb=17)
    try:
        assert isinstance(harness.store.pending_visuals, DiskPendingVisualStore)
        assert harness.store.pending_visuals.max_bytes == 17 * 1024 * 1024
    finally:
        await harness.bot.close()


# -- the two halves together ---------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_photo_held_at_chat_time_reaches_extraction_and_is_then_deleted(harness):
    from hina_bot.ai.memory_summary import MemorySummaryMixin

    await harness.send(text="히나야 안녕", id=1)
    await harness.send(text="히나야 우리 애 오늘 미용했어", id=2, visuals=[visual("2")])
    await harness.send(text="히나야 고마워", id=3)
    await harness.send(text="히나야 잘 자", id=4)
    assert harness.held("2") != {}

    reply = NS(status="completed", output_text=json.dumps({"items": [{
        "content": "강아지(사진상 말티즈로 보임)를 키움", "kind": "fact", "disclosure": "local",
        "confidence": 0.95, "source_message_ids": ["2"], "evidence": "우리 애 오늘 미용했어",
        "time_scope": "ongoing", "stability": "long", "usage_mode": "ambient",
        "visual_refs": ["2"],
    }]}, ensure_ascii=False))
    worker = MemorySummaryMixin()
    worker.settings = NS(
        memory_visual_input=True, model="fixed-model", model_routing_mode="fixed",
        memory_routing_smart_threshold=2.0, memory_output_tokens=4096,
        gemini_thinking_level="low", provider="openai", structured_memory_every=4,
    )
    worker.client = object()
    worker.usage = NS(request=AsyncMock(return_value=reply), routing_event=lambda *a, **k: None)

    assert await worker.extract_structured_memory(harness.store, SCOPE)

    (message,) = worker.usage.request.await_args.kwargs["input"]
    images = [block for block in message["content"] if block["type"] == "input_image"]
    assert len(images) == 1 and images[0]["image_url"].startswith("data:image/png;base64,")
    labels = [
        block["text"] for block in message["content"]
        if block.get("type") == "input_text" and block["text"].startswith("Untrusted")
    ]
    assert json.loads(labels[0].split(":", 1)[1])["message_id"] == "2"
    (item,) = harness.store.memory_items(100)
    assert item.confidence == 0.75 and item.source_message_ids == ("2",)
    assert harness.held("2") == {}

"""Memory extraction reads the photos held for its turns, then deletes them (#472, C3)."""

import json
import logging
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock

import pytest

from hina_bot.ai.memory_summary import MemorySummaryMixin
from hina_bot.ai.memory_visual import MEMORY_VISUAL_CONFIDENCE_CAP
from hina_bot.core.memory_annotations import LINT_VISUAL_GROUNDED
from hina_bot.core.pending_visuals import MemoryVisual
from hina_bot.core.routing import Scope
from hina_bot.core.store import Store
from hina_bot.tooling.flush_pending_memory import flush_pending

SCOPE = Scope(1, 10, 100, True)
UTTERANCE = "우리 애 오늘 미용하고 왔어"
PNG = b"\x89PNG\r\n\x1a\n" + b"photo-bytes"


def png(tag: str) -> bytes:
    return PNG + tag.encode()


class Harness(MemorySummaryMixin):
    pass


def make_harness(*responses, visual_input=True, events=None):
    harness = Harness()
    harness.settings = NS(
        summary_every=8, structured_memory_every=4, model="fixed-model",
        model_routing_mode="fixed", memory_routing_smart_threshold=2.0,
        memory_output_tokens=4096, gemini_thinking_level="low", provider="openai",
        memory_visual_input=visual_input,
    )
    harness.client = object()
    harness.usage = NS(
        request=AsyncMock(side_effect=responses),
        routing_event=(lambda name, **fields: events.append((name, fields)))
        if events is not None else (lambda *args, **kwargs: None),
    )
    return harness


def completed(items=()):
    return NS(status="completed", output_text=json.dumps({"items": list(items)}, ensure_ascii=False))


def grounded_item(**overrides):
    item = {
        "content": "강아지(사진상 말티즈로 보임)를 키움", "kind": "fact", "disclosure": "local",
        "confidence": 0.95, "source_message_ids": ["102"], "evidence": UTTERANCE,
        "time_scope": "ongoing", "stability": "long", "usage_mode": "ambient",
        "visual_refs": ["102"],
    }
    item.update(overrides)
    return item


@pytest.fixture
def store(tmp_path):
    value = Store.create(str(tmp_path / "hina.sqlite3"))
    for message_id in (101, 102, 103, 104):
        text = UTTERANCE if message_id == 102 else f"message-{message_id}"
        value.add(SCOPE, message_id, text, f"reply-{message_id}")
    yield value
    value.close()


def hold(store, message_id, tag="a", scope=SCOPE):
    assert store.pending_visuals.put(
        scope.realm, str(scope.channel_id), str(scope.user_id), str(message_id),
        [MemoryVisual(str(message_id), 1, "image/png", png(tag))],
    ) == 1


def held(store, *message_ids):
    return store.pending_visuals.get(
        SCOPE.realm, str(SCOPE.channel_id), str(SCOPE.user_id), [str(m) for m in message_ids]
    )


def stored_items(store):
    items = store.memory_items(100)
    annotations = store.memory_annotations([item.id for item in items])
    return [(item, annotations.get(item.id)) for item in items]


def request_input(harness, call=0):
    return harness.usage.request.await_args_list[call].kwargs["input"]


@pytest.mark.asyncio
async def test_the_photo_of_a_pending_turn_is_sent_with_its_message_id_then_deleted(store):
    hold(store, 102)
    harness = make_harness(completed())

    assert await harness.extract_structured_memory(store, SCOPE)

    (message,) = request_input(harness)
    blocks = message["content"]
    assert [block["type"] for block in blocks] == ["input_text", "input_text", "input_image"]
    assert json.loads(blocks[1]["text"].split(":", 1)[1]) == {"message_id": "102", "index": 1}
    assert blocks[2]["image_url"].startswith("data:image/png;base64,")
    assert store.memory_extraction_cursor(SCOPE) == store.history(SCOPE)[-1]["id"]
    assert held(store, 102) == {}  # deleted once the batch was committed


@pytest.mark.asyncio
async def test_the_request_stays_plain_text_when_no_photo_is_held(store):
    harness = make_harness(completed())

    await harness.extract_structured_memory(store, SCOPE)

    assert isinstance(request_input(harness), str)


@pytest.mark.asyncio
async def test_a_photo_backed_detail_is_flagged_and_capped(store):
    hold(store, 102)
    harness = make_harness(completed([grounded_item()]))

    await harness.extract_structured_memory(store, SCOPE)

    ((item, annotation),) = stored_items(store)
    assert item.confidence == MEMORY_VISUAL_CONFIDENCE_CAP
    assert LINT_VISUAL_GROUNDED in annotation.lint_flags
    assert annotation.evidence == UTTERANCE


@pytest.mark.asyncio
async def test_a_memory_resting_on_the_photo_alone_is_not_stored(store):
    hold(store, 102)
    photo_only = grounded_item(evidence="사진 속 말티즈")
    harness = make_harness(completed([photo_only]))

    assert await harness.extract_structured_memory(store, SCOPE)

    assert stored_items(store) == []
    assert store.memory_extraction_cursor(SCOPE) == store.history(SCOPE)[-1]["id"]
    assert held(store, 102) == {}


@pytest.mark.asyncio
async def test_photos_are_ignored_but_still_deleted_when_the_feature_is_off(store):
    hold(store, 102)
    harness = make_harness(completed([grounded_item()]), visual_input=False)

    await harness.extract_structured_memory(store, SCOPE)

    assert isinstance(request_input(harness), str)
    ((item, annotation),) = stored_items(store)
    assert item.confidence == 0.95 and LINT_VISUAL_GROUNDED not in annotation.lint_flags
    assert held(store, 102) == {}  # an extracted turn's photo never outlives it


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [
    RuntimeError("provider rejected the image"),
    NS(status="completed", output_text="   "),
    NS(status="failed", output_text=""),
])
async def test_a_failed_request_with_photos_is_repeated_on_the_text_alone(store, failure):
    hold(store, 102)
    events = []
    harness = make_harness(failure, completed([grounded_item()]), events=events)

    assert await harness.extract_structured_memory(store, SCOPE)

    assert harness.usage.request.await_count == 2
    assert isinstance(request_input(harness, 0), list)
    assert isinstance(request_input(harness, 1), str)
    # The retry carried no photo, so a visual claim in its answer is not honoured.
    ((item, annotation),) = stored_items(store)
    assert item.confidence == 0.95 and LINT_VISUAL_GROUNDED not in annotation.lint_flags
    assert held(store, 102) == {}
    completed_event = next(f for n, f in events if f["status"] == "completed")
    assert completed_event["visual_fallback"] is True
    assert completed_event["visual_images"] == 1 and completed_event["visual_grounded_items"] == 0


@pytest.mark.asyncio
async def test_if_the_text_retry_fails_too_the_batch_and_photo_wait(store):
    hold(store, 102)
    harness = make_harness(RuntimeError("down"), RuntimeError("still down"))

    assert not await harness.extract_structured_memory(store, SCOPE)

    assert store.memory_extraction_cursor(SCOPE) == 0
    assert len(held(store, 102)["102"]) == 1


@pytest.mark.asyncio
async def test_an_invalid_answer_keeps_the_photo_for_the_retry(store):
    hold(store, 102)
    harness = make_harness(NS(status="completed", output_text="not-json"), completed())

    assert not await harness.extract_structured_memory(store, SCOPE)
    assert len(held(store, 102)["102"]) == 1

    assert await harness.extract_structured_memory(store, SCOPE)
    assert isinstance(request_input(harness, 1), list)  # the retry still has the photo
    assert held(store, 102) == {}


@pytest.mark.asyncio
async def test_a_request_carries_only_the_newest_photos_within_the_limits(store):
    for message_id, tag in ((101, "a"), (102, "b"), (103, "c"), (104, "d")):
        store.pending_visuals.put(
            SCOPE.realm, "10", "100", str(message_id),
            [MemoryVisual(str(message_id), n, "image/png", png(f"{tag}{n}")) for n in (1, 2)],
        )
    harness = make_harness(completed())

    await harness.extract_structured_memory(store, SCOPE)

    (message,) = request_input(harness)
    labels = [
        json.loads(block["text"].split(":", 1)[1])
        for block in message["content"]
        if block.get("type") == "input_text" and block["text"].startswith("Untrusted")
    ]
    assert [(label["message_id"], label["index"]) for label in labels] == [
        ("103", 1), ("103", 2), ("104", 1), ("104", 2),
    ]
    # Every held photo of the batch is removed, including the ones not sent.
    assert held(store, 101, 102, 103, 104) == {}


@pytest.mark.asyncio
async def test_a_photo_read_failure_falls_back_to_text_without_logging_details(
    store, monkeypatch, caplog
):
    hold(store, 102)

    def broken(*args, **kwargs):
        raise OSError("denied: /secret/location")

    monkeypatch.setattr(store.pending_visuals, "get", broken)
    events = []
    harness = make_harness(completed(), events=events)

    with caplog.at_level(logging.WARNING, logger="hina"):
        assert await harness.extract_structured_memory(store, SCOPE)

    assert isinstance(request_input(harness), str)
    assert "Pending visual read failed (OSError)" in caplog.text
    assert "/secret/location" not in caplog.text
    requested = next(f for n, f in events if f["status"] == "requested")
    assert requested["visual_images"] == 0


@pytest.mark.asyncio
async def test_a_cleanup_failure_does_not_undo_the_committed_batch(store, monkeypatch, caplog):
    hold(store, 102)

    def broken(*args, **kwargs):
        raise OSError("denied: /secret/location")

    monkeypatch.setattr(store.pending_visuals, "discard_messages", broken)
    harness = make_harness(completed())

    with caplog.at_level(logging.WARNING, logger="hina"):
        assert await harness.extract_structured_memory(store, SCOPE)

    assert store.memory_extraction_cursor(SCOPE) == store.history(SCOPE)[-1]["id"]
    assert "Pending visual cleanup failed (OSError)" in caplog.text
    assert "/secret/location" not in caplog.text
    # The sweep removes it later: its turn is no longer pending.
    removed = store.pending_visuals.sweep(store.pending_visual_keys(), max_age_seconds=3600)
    assert removed == 1


@pytest.mark.asyncio
async def test_telemetry_reports_counts_only(store):
    hold(store, 102, tag="secret-photo")
    events = []
    harness = make_harness(completed([grounded_item()]), events=events)

    await harness.extract_structured_memory(store, SCOPE)

    requested = next(f for n, f in events if f["status"] == "requested")
    done = next(f for n, f in events if f["status"] == "completed")
    assert (requested["visual_images"], requested["visual_bytes"]) == (1, len(png("secret-photo")))
    assert done["visual_grounded_items"] == 1 and "visual_fallback" not in done
    assert "secret-photo" not in json.dumps(events, ensure_ascii=False, default=str)


def test_a_store_without_pending_visuals_is_simply_text_only():
    harness = make_harness()
    bare = NS()

    assert harness._pending_visuals_for(bare, SCOPE, ["102"]) == []
    harness._discard_extracted_visuals(bare, SCOPE, ["102"])  # no error


@pytest.mark.asyncio
async def test_the_offline_flush_uses_the_held_photos_too(store):
    hold(store, 102)
    harness = make_harness(completed())

    batches, failures = await flush_pending(store, harness)

    assert (batches, failures) == (1, ())
    assert isinstance(request_input(harness), list)
    assert held(store, 102) == {}

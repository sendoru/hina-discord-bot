"""End-to-end pending audio extraction without real provider calls (#548 P2)."""

import json
import logging
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock

import pytest

from hina_bot.ai.memory_summary import MemorySummaryMixin
from hina_bot.core.memory_annotations import LINT_AUDIO_GROUNDED
from hina_bot.core.pending_audio import MemoryAudio
from hina_bot.core.pending_visuals import MemoryVisual
from hina_bot.core.routing import Scope
from hina_bot.core.store import Store

SCOPE = Scope(1, 10, 100, True)
OGG = b"OggS" + b"private-test-voice"
PNG = b"\x89PNG\r\n\x1a\n" + b"photo"


class Worker(MemorySummaryMixin):
    pass


def harness(*responses, audio=True, visual=False, provider="gemini", events=None):
    worker = Worker()
    worker.settings = NS(
        summary_every=8, structured_memory_every=4, model="gemini-3.5-flash-lite",
        model_routing_mode="fixed", memory_routing_smart_threshold=2.0,
        memory_output_tokens=4096, gemini_thinking_level="low", provider=provider,
        memory_audio_input=audio, memory_visual_input=visual,
    )
    worker.client = object()
    worker.usage = NS(
        request=AsyncMock(side_effect=responses),
        routing_event=(lambda name, **kw: events.append((name, kw)))
        if events is not None else (lambda *a, **kw: None),
    )
    return worker


def completed(items=()):
    return NS(status="completed", output_text=json.dumps({"items": list(items)}, ensure_ascii=False))


def verdict(value="keep", evidence="커피 별로 안 좋아해"):
    return NS(status="completed", output_text=json.dumps({
        "results": [{"index": 0, "verdict": value, "evidence": evidence}],
    }, ensure_ascii=False))


def audio_item(**kw):
    item = {
        "content": "커피를 좋아하지 않음", "kind": "preference", "disclosure": "global",
        "confidence": 0.98, "source_message_ids": ["102"],
        "audio_refs": ["102"], "evidence": "커피 별로 안 좋아해",
        "usage_mode": "ambient", "time_scope": "ongoing", "stability": "long",
    }
    item.update(kw)
    return item


@pytest.fixture
def store(tmp_path):
    value = Store.create(str(tmp_path / "hina.sqlite3"))
    for mid in (101, 102, 103, 104):
        user = ("히나야\n[첨부 오디오: voice.ogg; 재생 길이 3.5초; 원본은 별도 입력]"
                if mid == 102 else f"plain text {mid}")
        value.add(SCOPE, mid, user, f"reply-{mid}")
    yield value
    value.close()


def hold(store, message_id="102"):
    stored = store.pending_audio.put(
        SCOPE.realm, "10", "100", str(message_id),
        [MemoryAudio(str(message_id), 1, "audio/ogg", OGG)],
    )
    assert stored == 1


def holding(store):
    return store.pending_audio.get(SCOPE.realm, "10", "100", ["102"])


def results(store):
    rows = store.memory_items(100)
    annotations = store.memory_annotations([item.id for item in rows])
    return [(item, annotations.get(item.id)) for item in rows]


def request(worker, index):
    return worker.usage.request.await_args_list[index].kwargs


@pytest.mark.asyncio
async def test_verified_audio_only_memory_is_stored_then_original_deleted(store):
    hold(store)
    events = []
    worker = harness(completed([audio_item()]), verdict(), events=events)
    assert await worker.extract_structured_memory(store, SCOPE)
    assert [request(worker, i)["input"][0]["content"][-1]["type"] for i in (0, 1)] == [
        "input_audio", "input_audio",
    ]
    assert [worker.usage.request.await_args_list[i].args[1] for i in (0, 1)] == [
        "extract_memory_items_shadow", "verify_memory_audio",
    ]
    ((entry, annotation),) = results(store)
    assert entry.source_message_ids == ("102",)
    assert entry.confidence == 0.75 and entry.disclosure.value == "local"
    assert annotation.evidence == "커피 별로 안 좋아해"
    assert LINT_AUDIO_GROUNDED in annotation.lint_flags
    assert holding(store) == {}
    assert store.memory_extraction_cursor(SCOPE) == store.history(SCOPE)[-1]["id"]
    done = next(fields for name, fields in events
                if name == "memory.shadow_extraction" and fields["status"] == "completed")
    assert done["audio_grounded_items"] == 1 and done["audio_verified_candidates"] == 1
    assert done["audio_files"] == 1 and "private-test-voice" not in json.dumps(events)


@pytest.mark.asyncio
@pytest.mark.parametrize("verifier", [
    verdict("reject", ""), NS(status="completed", output_text="{bad"), RuntimeError("offline"),
])
async def test_rejected_or_failed_audio_verifier_cannot_store_audio_fact(store, verifier):
    hold(store)
    worker = harness(completed([audio_item()]), verifier)
    assert await worker.extract_structured_memory(store, SCOPE)
    assert results(store) == []
    assert holding(store) == {}


@pytest.mark.asyncio
async def test_legacy_text_memory_survives_failed_audio_validation(store):
    hold(store)
    text_item = {
        "content": "테스트 101 발화에 관한 사실", "kind": "fact", "disclosure": "local",
        "confidence": 0.9, "source_message_ids": ["101"], "evidence": "plain text 101",
        "usage_mode": "contextual",
    }
    worker = harness(completed([audio_item(), text_item]), RuntimeError("audio verify failed"))
    assert await worker.extract_structured_memory(store, SCOPE)
    assert [row.content for row, _ in results(store)] == ["테스트 101 발화에 관한 사실"]
    assert holding(store) == {}


@pytest.mark.asyncio
async def test_failed_native_audio_extraction_retries_plain_text_and_drops_audio_claim(store):
    hold(store)
    worker = harness(RuntimeError("audio unavailable"), completed([audio_item()]))
    assert await worker.extract_structured_memory(store, SCOPE)
    assert isinstance(request(worker, 0)["input"], list)
    assert isinstance(request(worker, 1)["input"], str)
    assert results(store) == [] and holding(store) == {}


@pytest.mark.asyncio
async def test_failing_every_extraction_attempt_preserves_cursor_and_audio(store):
    hold(store)
    worker = harness(RuntimeError("audio unavailable"), RuntimeError("text unavailable"))
    assert not await worker.extract_structured_memory(store, SCOPE)
    assert store.memory_extraction_cursor(SCOPE) == 0
    assert holding(store)["102"][0].data == OGG


@pytest.mark.asyncio
async def test_invalid_completed_extraction_preserves_audio_for_retry(store):
    hold(store)
    worker = harness(NS(status="completed", output_text="not json"))
    assert not await worker.extract_structured_memory(store, SCOPE)
    assert holding(store)["102"][0].data == OGG


@pytest.mark.asyncio
async def test_disabled_opt_in_or_unsupported_provider_discard_staged_audio(store):
    hold(store)
    worker = harness(completed(), audio=False)
    assert await worker.extract_structured_memory(store, SCOPE)
    assert isinstance(request(worker, 0)["input"], str)
    assert holding(store) == {}


@pytest.mark.asyncio
async def test_mixed_image_audio_input_and_image_only_fallback(store):
    hold(store)
    assert store.pending_visuals.put(
        SCOPE.realm, "10", "100", "102",
        [MemoryVisual("102", 1, "image/png", PNG)],
    ) == 1
    worker = harness(RuntimeError("audio rejected"), completed(), visual=True)
    assert await worker.extract_structured_memory(store, SCOPE)
    before = request(worker, 0)["input"][0]["content"]
    after = request(worker, 1)["input"][0]["content"]
    assert [x["type"] for x in before] == [
        "input_text", "input_text", "input_image", "input_text", "input_audio",
    ]
    assert [x["type"] for x in after] == ["input_text", "input_text", "input_image"]
    assert holding(store) == {}


@pytest.mark.asyncio
async def test_audio_cleanup_failure_isolated_and_swept_later(store, monkeypatch, caplog):
    hold(store)
    worker = harness(completed())
    def broken(*a, **kw):
        raise OSError("sensitive/user/path")
    monkeypatch.setattr(store.pending_audio, "discard_messages", broken)
    with caplog.at_level(logging.WARNING, logger="hina"):
        assert await worker.extract_structured_memory(store, SCOPE)
    assert "Pending audio cleanup failed (OSError)" in caplog.text
    assert "sensitive/user/path" not in caplog.text
    assert store.pending_audio.sweep(store.pending_visual_keys(), max_age_seconds=3600) == 1


@pytest.mark.asyncio
async def test_audio_only_ongoing_style_directive_is_independently_verified(store):
    hold(store)
    command = audio_item(
        content="앞으로 답변을 귀엽게 해주길 원함",
        usage_mode="directive", directive_type="style",
        evidence="앞으로도 계속 귀엽게 말해줘",
    )
    spoken_quote = "앞으로도 계속 귀엽게 말해줘"
    directive_result = NS(status="completed", output_text=json.dumps({
        "results": [{"index": 0, "verdict": "keep"}],
    }))
    worker = harness(completed([command]), verdict(evidence=spoken_quote), directive_result)

    assert await worker.extract_structured_memory(store, SCOPE)
    ((entry, annotation),) = results(store)
    assert entry.content == "앞으로 답변을 귀엽게 해주길 원함"
    assert annotation.verified is True
    assert annotation.usage_mode.value == "directive"
    assert annotation.directive_type.value == "style"
    assert annotation.evidence == spoken_quote
    payload = json.loads(request(worker, 2)["input"])
    assert payload["items"][0]["audio_verified"] is True
    assert payload["items"][0]["source_turns"][0]["audio_verified_evidence"] == spoken_quote
    assert holding(store) == {}


@pytest.mark.asyncio
async def test_audio_store_not_read_for_non_gemini_provider(store):
    hold(store)
    worker = harness(completed(), provider="openai")
    assert await worker.extract_structured_memory(store, SCOPE)
    assert isinstance(request(worker, 0)["input"], str)
    assert holding(store) == {}

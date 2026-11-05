"""Image input for structured-memory extraction (#472): request shape and parser guards."""

import base64
import json
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock

import pytest

from hina_bot.ai.memory_extraction import parse_shadow_extraction
from hina_bot.ai.memory_model_routing import build_memory_model_plan
from hina_bot.ai.memory_summary import MemorySummaryMixin
from hina_bot.ai.memory_visual import (
    MEMORY_VISUAL_CONFIDENCE_CAP,
    MEMORY_VISUAL_MAX_BYTES,
    MemoryVisual,
    extraction_input,
    select_memory_visuals,
    visual_message_ids,
)
from hina_bot.core.image_mime import sniff_image_mime
from hina_bot.core.memory_annotations import LINT_EVIDENCE_MISMATCH, LINT_VISUAL_GROUNDED

PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg=="
)
JPEG = b"\xff\xd8\xff\xe0" + b"jpeg-bytes"
GIF = b"GIF89a" + b"gif-bytes"
WEBP = b"RIFF\x00\x00\x00\x00WEBPVP8 "

UTTERANCE = "우리 애 오늘 미용하고 왔어"
SOURCE_TURNS = {
    "m1": {"user": UTTERANCE, "at": "2026-10-30 11:55:00"},
    "m2": {"user": "이거 봐", "at": "2026-10-30 11:58:00"},
}


def _item(**overrides):
    item = {
        "content": "강아지(사진상 말티즈로 보임)를 키움",
        "kind": "fact",
        "disclosure": "local",
        "confidence": 0.95,
        "source_message_ids": ["m1"],
        "evidence": UTTERANCE,
        "time_scope": "ongoing",
        "stability": "long",
        "usage_mode": "ambient",
        "visual_refs": ["m1"],
    }
    item.update(overrides)
    return item


def _parse(items, *, visual=frozenset({"m1"})):
    return parse_shadow_extraction(
        json.dumps({"items": items}, ensure_ascii=False),
        allowed_source_ids={"m1", "m2"},
        source_turns=SOURCE_TURNS,
        visual_message_ids=visual,
    )


def test_sniff_image_mime_uses_the_signature_not_a_name():
    assert sniff_image_mime(PNG) == "image/png"
    assert sniff_image_mime(JPEG) == "image/jpeg"
    assert sniff_image_mime(GIF) == "image/gif"
    assert sniff_image_mime(WEBP) == "image/webp"
    assert sniff_image_mime(b"%PDF-1.7") is None
    assert sniff_image_mime(b"") is None


def test_extraction_input_stays_the_payload_string_without_images():
    assert extraction_input('{"turns":[]}') == '{"turns":[]}'
    assert extraction_input('{"turns":[]}', []) == '{"turns":[]}'


def test_extraction_input_attaches_labelled_images_after_the_payload():
    visuals = [MemoryVisual("m1", 1, "image/png", PNG), MemoryVisual("m1", 2, "image/jpeg", JPEG)]

    (message,) = extraction_input('{"turns":[]}', visuals)

    assert message["role"] == "user"
    blocks = message["content"]
    assert blocks[0] == {"type": "input_text", "text": '{"turns":[]}'}
    assert [block["type"] for block in blocks] == ["input_text"] + ["input_text", "input_image"] * 2
    label = blocks[1]["text"]
    assert label.startswith("Untrusted visual evidence metadata(JSON):")
    assert json.loads(label.split(":", 1)[1]) == {"message_id": "m1", "index": 1}
    assert blocks[2]["image_url"].startswith("data:image/png;base64,")
    assert base64.b64decode(blocks[2]["image_url"].split(",", 1)[1]) == PNG
    assert all("resolution" not in block for block in blocks)

    (with_resolution,) = extraction_input('{"turns":[]}', visuals, include_media_resolution=True)
    assert [b["resolution"] for b in with_resolution["content"] if b["type"] == "input_image"] == [
        "medium", "medium",
    ]


def test_select_memory_visuals_keeps_the_newest_that_fit_in_chronological_order():
    visuals = [MemoryVisual(f"m{i}", 1, "image/png", PNG) for i in range(1, 7)]

    chosen = select_memory_visuals(visuals, max_images=3)

    assert [v.message_id for v in chosen] == ["m4", "m5", "m6"]


def test_select_memory_visuals_skips_what_does_not_fit_or_is_not_an_image():
    big = MemoryVisual("m1", 1, "image/png", b"x" * 100)
    small = MemoryVisual("m2", 1, "image/png", b"y" * 10)
    pdf = MemoryVisual("m3", 1, "application/pdf", b"z" * 10)
    empty = MemoryVisual("m4", 1, "image/png", b"")

    chosen = select_memory_visuals([big, small, pdf, empty], max_bytes=50)

    assert [v.message_id for v in chosen] == ["m2"]
    assert MEMORY_VISUAL_MAX_BYTES == 8 * 1024 * 1024


def test_visual_message_ids_names_the_turns_with_images():
    visuals = [MemoryVisual("m1", 1, "image/png", PNG), MemoryVisual("m1", 2, "image/png", PNG)]
    assert visual_message_ids(visuals) == frozenset({"m1"})
    assert visual_message_ids([]) == frozenset()


def test_visual_grounded_item_is_flagged_and_confidence_is_capped():
    parsed = _parse([_item()])

    (item,) = parsed.items
    assert parsed.rejected_items == 0
    assert item.confidence == MEMORY_VISUAL_CONFIDENCE_CAP
    assert LINT_VISUAL_GROUNDED in item.annotation.lint_flags
    # The claim is still carried by the user's own words.
    assert item.annotation.evidence == UTTERANCE


def test_lower_confidence_is_not_raised_by_the_cap():
    (item,) = _parse([_item(confidence=0.6)]).items
    assert item.confidence == 0.6


def test_visual_refs_are_ignored_when_that_turn_had_no_image():
    (item,) = _parse([_item(source_message_ids=["m2"], visual_refs=["m2"],
                            evidence="이거 봐")]).items
    # m2 is a source but only m1 carried an image in this request.
    assert item.confidence == 0.95
    assert LINT_VISUAL_GROUNDED not in item.annotation.lint_flags


def test_visual_refs_are_ignored_when_the_request_had_no_images():
    (item,) = _parse([_item()], visual=frozenset()).items
    assert item.confidence == 0.95
    assert LINT_VISUAL_GROUNDED not in item.annotation.lint_flags


def test_visual_refs_must_point_at_a_cited_source_turn():
    (item,) = _parse([_item(source_message_ids=["m2"], evidence="이거 봐")],
                     visual=frozenset({"m1"})).items
    assert LINT_VISUAL_GROUNDED not in item.annotation.lint_flags


@pytest.mark.parametrize("refs", ["m1", 1, {"m1": True}, [1], None])
def test_malformed_visual_refs_are_ignored(refs):
    (item,) = _parse([_item(visual_refs=refs)]).items
    assert item.confidence == 0.95
    assert LINT_VISUAL_GROUNDED not in item.annotation.lint_flags


def test_a_photo_alone_never_justifies_a_memory():
    for evidence in ("", "사진에 말티즈가 보임", "다른 발화"):
        parsed = _parse([_item(evidence=evidence)])
        assert parsed.items == ()
        assert parsed.rejected_items == 1


def test_ungrounded_item_with_a_bad_quote_keeps_the_existing_downgrade():
    # Without a visual claim the established behaviour is unchanged: keep, flag, quieten.
    (item,) = _parse([_item(visual_refs=None, evidence="없는 발화")]).items
    assert LINT_EVIDENCE_MISMATCH in item.annotation.lint_flags
    assert item.annotation.usage_mode.value == "contextual"


def test_relationship_items_ignore_visual_refs():
    relationship = {
        "content": "편하게 장난을 주고받음", "kind": "relationship", "disclosure": "implicit",
        "confidence": 0.9, "source_message_ids": ["m1"], "visual_refs": ["m1"],
        "relationship_evidence": {"comfort": 2},
    }
    (item,) = _parse([relationship]).items
    assert item.confidence == 0.9
    assert item.annotation is None


class Worker(MemorySummaryMixin):
    def __init__(self, provider="openai"):
        self.settings = NS(
            model="m", model_routing_mode="fixed", memory_routing_smart_threshold=2.0,
            memory_output_tokens=1024, provider=provider, runtime_timezone="Asia/Seoul",
            gemini_thinking_level="low",
        )
        self.client = object()
        self.usage = NS(request=AsyncMock(return_value=NS(status="completed", output_text="{}")))


@pytest.mark.asyncio
async def test_memory_request_sends_plain_text_without_images():
    worker = Worker()
    payload = {"turns": [{"message_id": "m1", "user": "안녕"}]}
    plan = build_memory_model_plan(worker.settings, "", payload, shared=False)

    await worker._memory_request("extract_memory_items_shadow", "지침", payload, plan)

    kwargs = worker.usage.request.await_args.kwargs
    assert kwargs["input"] == json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


@pytest.mark.asyncio
async def test_memory_request_sends_image_blocks_when_images_are_given():
    worker = Worker()
    payload = {"turns": [{"message_id": "m1", "user": UTTERANCE}]}
    plan = build_memory_model_plan(worker.settings, "", payload, shared=False)

    await worker._memory_request(
        "extract_memory_items_shadow", "지침", payload, plan,
        images=[MemoryVisual("m1", 1, "image/png", PNG)],
    )

    kwargs = worker.usage.request.await_args.kwargs
    (message,) = kwargs["input"]
    assert message["content"][0]["text"] == json.dumps(
        payload, ensure_ascii=False, separators=(",", ":")
    )
    assert [b["type"] for b in message["content"]] == ["input_text", "input_text", "input_image"]
    assert kwargs["instructions"] == "지침"


@pytest.mark.asyncio
async def test_gemini_3_requests_ask_for_medium_image_resolution():
    worker = Worker(provider="gemini")
    worker.settings.model = "gemini-3-flash"
    payload = {"turns": []}
    plan = build_memory_model_plan(worker.settings, "", payload, shared=False)

    await worker._memory_request(
        "extract_memory_items_shadow", "지침", payload, plan,
        images=[MemoryVisual("m1", 1, "image/png", PNG)],
    )

    (message,) = worker.usage.request.await_args.kwargs["input"]
    assert message["content"][-1]["resolution"] == "medium"

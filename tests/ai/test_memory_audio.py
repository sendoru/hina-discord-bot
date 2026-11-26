"""Unit guards for native audio extraction input and independent evidence verdicts."""

import json

from hina_bot.ai.memory_audio import (
    MEMORY_AUDIO_CONFIDENCE_CAP,
    audio_message_ids,
    select_memory_audios,
    verification_candidates,
    verified_audio_evidence,
)
from hina_bot.ai.memory_extraction import parse_shadow_extraction
from hina_bot.ai.memory_visual import extraction_input
from hina_bot.core.memory_annotations import LINT_AUDIO_GROUNDED
from hina_bot.core.pending_audio import MemoryAudio

OGG = b"OggS" + b"sample"


def clip(mid, n=1, data=OGG):
    return MemoryAudio(str(mid), n, "audio/ogg", data)


def item(**kw):
    value = {
        "content": "커피를 좋아하지 않음", "kind": "preference", "disclosure": "global",
        "confidence": 0.99, "source_message_ids": ["102"],
        "evidence": "커피 별로 안 좋아해", "audio_refs": ["102"],
        "usage_mode": "ambient", "time_scope": "ongoing", "stability": "long",
    }
    value.update(kw)
    return value


def parse(items, *, audios=frozenset({"102"}), source_audio=frozenset({"102"}), verified=None):
    return parse_shadow_extraction(
        json.dumps({"items": items}, ensure_ascii=False),
        allowed_source_ids={"101", "102"},
        source_turns={
            "101": {"user": "나는 춤을 좋아해", "at": "2026-11-24 08:00:00"},
            "102": {"user": "히나야\n[첨부 오디오: voice.ogg; 재생 길이 3초]",
                    "at": "2026-11-24 08:05:00"},
        },
        audio_message_ids=audios,
        audio_source_message_ids=source_audio,
        verified_audio_quotes=verified or {},
    )


def test_audio_input_has_traceable_message_id_and_native_audio_block():
    content = extraction_input('{"turns":[]}', audios=[clip(102)])
    (message,) = content
    blocks = message["content"]
    assert [x["type"] for x in blocks] == ["input_text", "input_text", "input_audio"]
    assert json.loads(blocks[1]["text"].split(":", 1)[1]) == {"message_id": "102", "index": 1}
    assert blocks[2]["audio_url"].startswith("data:audio/ogg;base64,")
    assert extraction_input('{"turns":[]}', audios=[]) == '{"turns":[]}'


def test_audio_selection_is_bounded_and_latest_first():
    audios = [clip("101", data=b"A" * 100), clip("102", data=b"B" * 100),
              clip("103", data=b"C" * 100)]
    selected = select_memory_audios(audios, max_files=2, max_bytes=200)
    assert [x.message_id for x in selected] == ["102", "103"]
    assert select_memory_audios(audios, max_bytes=90) == []
    assert audio_message_ids(selected) == frozenset({"102", "103"})


def test_audio_candidates_are_validated_against_attached_sources():
    raw = json.dumps({"items": [item(), item(source_message_ids=["101"]),
                                item(audio_refs=["99"]), item(kind="relationship")]},
                     ensure_ascii=False)
    assert verification_candidates(raw, [clip("102")]) == [{
        "index": 0, "content": "커피를 좋아하지 않음",
        "evidence": "커피 별로 안 좋아해", "audio_refs": ["102"],
    }]


def test_only_explicit_short_unique_audio_verdicts_are_accepted():
    candidates = [{"index": 0}, {"index": 2}]
    data = {"results": [
        {"index": 0, "verdict": "keep", "evidence": "커피는 별로"},
        {"index": 0, "verdict": "keep", "evidence": "중복"},
        {"index": 2, "verdict": "reject", "evidence": "아님"},
        {"index": 4, "verdict": "keep", "evidence": "미등록"},
    ]}
    assert verified_audio_evidence(json.dumps(data, ensure_ascii=False), candidates) == {
        0: "커피는 별로",
    }
    assert verified_audio_evidence("not json", candidates) == {}


def test_audio_quote_bypasses_text_substring_only_when_second_pass_verified():
    parsed = parse([item()], verified={0: "커피 별로 안 좋아해"})
    assert parsed.rejected_items == 0
    (entry,) = parsed.items
    assert entry.confidence == MEMORY_AUDIO_CONFIDENCE_CAP
    assert entry.disclosure.value == "local"
    assert entry.annotation.evidence == "커피 별로 안 좋아해"
    assert entry.annotation.usage_mode.value == "contextual"
    assert entry.annotation.audience.value == "owner"
    assert LINT_AUDIO_GROUNDED in entry.annotation.lint_flags


def test_unverified_or_forged_audio_refs_are_rejected_not_converted_to_text():
    cases = [
        (item(), {}, frozenset({"102"})),
        (item(), {0: "커피 별로 안 좋아해"}, frozenset()),
        (item(audio_refs=["100"]), {0: "커피 별로 안 좋아해"}, frozenset({"102"})),
        (item(audio_refs=["102", "101"]), {0: "커피 별로 안 좋아해"}, frozenset({"102"})),
        (item(kind="relationship"), {0: "커피 별로 안 좋아해"}, frozenset({"102"})),
        (item(audio_refs=[]), {0: "커피 별로 안 좋아해"}, frozenset({"102"})),
    ]
    for candidate, verified, audio_ids in cases:
        parsed = parse([candidate], audios=audio_ids, verified=verified)
        assert parsed.items == () and parsed.rejected_items == 1


def test_audio_claim_without_refs_does_not_use_metadata_as_a_quote():
    parsed = parse([item(audio_refs=None, evidence="첨부 오디오")])
    assert parsed.items == () and parsed.rejected_items == 1
    parsed = parse([item(audio_refs=None, evidence="커피 별로 안 좋아해")])
    assert parsed.items == () and parsed.rejected_items == 1


def test_text_grounded_item_remains_accepted_alongside_audio_turn():
    candidate = item(
        content="춤을 좋아함", source_message_ids=["101", "102"],
        audio_refs=None, evidence="나는 춤을 좋아해",
    )
    (entry,) = parse([candidate]).items
    assert entry.annotation.evidence == "나는 춤을 좋아해"
    assert LINT_AUDIO_GROUNDED not in entry.annotation.lint_flags

import io
import wave
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from hina_bot.ai.audio import AudioInput, augment_audio_input
from hina_bot.ai.providers import _gemini_content, _gemini_generate_content_parts
from hina_bot.core.routing import Scope
from hina_bot.discord.audio import (
    AudioLimits,
    audio_context_refs,
    collect_audio_inputs,
    message_has_audio,
)
from hina_bot.discord.reply_context import REPLY_CONTEXT
from hina_bot.discord.target_recent import TargetAwareRecentMessages
from hina_bot.discord.turn_provenance import CURRENT_TURN_PROVENANCE, build_turn_provenance


def wav_bytes(seconds=1):
    output = io.BytesIO()
    with wave.open(output, "wb") as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(8000)
        out.writeframes(b"\x00\x00" * 8000 * seconds)
    return output.getvalue()


class Attachment:
    def __init__(self, data, name="recording.wav", content_type="audio/wav", duration=None):
        self.data = data
        self.size = len(data)
        self.filename = name
        self.content_type = content_type
        self.duration_secs = duration

    async def read(self):
        return self.data


def message(mid, attachments=(), *, channel=None):
    return SimpleNamespace(
        id=mid,
        attachments=list(attachments),
        content="",
        author=SimpleNamespace(id=10, display_name="someone"),
        flags=SimpleNamespace(voice=bool(attachments)),
        created_at=datetime.now(UTC),
        channel=channel,
    )


@pytest.mark.asyncio
async def test_native_audio_current_does_not_fetch_passive_recent():
    previous = message(10, [Attachment(wav_bytes(2))])
    current = message(11, [Attachment(wav_bytes(1))])
    fetched = []
    async def fetch(mid):
        fetched.append(mid)
        return previous
    current.channel = SimpleNamespace(fetch_message=fetch)
    refs = audio_context_refs([{
        "message_id": "10", "context_kind": "channel_ambient",
        "reference_strength": "passive_recent", "has_audio": True,
    }])
    result = await collect_audio_inputs(
        current, context_refs=refs,
        limits=AudioLimits(attachments=2),
    )
    assert message_has_audio(current)
    assert [entry.message_id for entry in result] == ["11"]
    assert fetched == []
    assert result[0].mime_type == "audio/wav"
    assert result[0].duration_seconds == 1.0


@pytest.mark.asyncio
async def test_audio_reply_is_not_subject_to_recent_limit():
    earlier = message(8, [Attachment(wav_bytes(2))])
    current = message(11)
    async def fetch(mid):
        assert mid == 8
        return earlier
    current.channel = SimpleNamespace(fetch_message=fetch)
    refs = audio_context_refs([{
        "message_id": "8", "context_kind": "replied_message",
        "reference_strength": "explicit_reply", "has_audio": True,
    }])
    result = await collect_audio_inputs(
        current, context_refs=refs,
        limits=AudioLimits(attachments=2),
    )
    assert len(result) == 1
    assert result[0].message_id == "8"
    assert result[0].reference_strength == "explicit_reply"


def test_audio_content_uses_gemini_native_blocks_not_transcription():
    raw = wav_bytes(1)
    entry = AudioInput(
        data=raw, mime_type="audio/wav", source="attachment",
        context_kind="current_message", reference_strength="current_message",
        message_id="7", author_user_id="10", author_name="person",
        message_content="", at="", name="record.wav", duration_seconds=1,
    )
    request = augment_audio_input(
        [{"role": "user", "content": "이게 무슨 소리야?"}], (entry,)
    )
    parts = _gemini_content(request[0]["content"])
    assert any(part["type"] == "audio" and part["mime_type"] == "audio/wav" for part in parts)
    generated = _gemini_generate_content_parts(request[0]["content"])
    assert any(part.get("inline_data", {}).get("mime_type") == "audio/wav" for part in generated)
    assert all("transcription" not in str(part).lower() for part in generated)


def test_historical_audio_is_untrusted_reference_not_current_speech():
    entry = AudioInput(
        data=wav_bytes(1), mime_type="audio/wav", source="attachment",
        context_kind="recent_channel_message", reference_strength="passive_recent",
        message_id="9", author_user_id="20", author_name="another person",
        message_content="음악", at="", name="clip.wav", duration_seconds=1,
    )
    request = augment_audio_input(
        [
            {"role": "user", "content": "신뢰할 수 없는 참고 데이터(JSON): {}"},
            {"role": "user", "content": "그 소리 뭐였어?"},
        ],
        (entry,),
    )
    assert isinstance(request[0]["content"], list)
    assert any(block.get("type") == "input_audio" for block in request[0]["content"])
    assert request[1]["content"] == "그 소리 뭐였어?"



def test_only_explicit_reply_audio_is_automatically_selected():
    rows = [
        {"message_id": "1", "has_audio": True,
         "context_kind": "speaker_thread", "reference_strength": "same_speaker"},
        {"message_id": "2", "has_audio": True,
         "context_kind": "channel_ambient", "reference_strength": "passive_recent"},
        {"message_id": "3", "has_audio": True,
         "context_kind": "target_user_history", "reference_strength": "prior_history"},
        {"message_id": "4", "has_audio": True,
         "context_kind": "prior_reply_source", "reference_strength": "prior_reference_material"},
        {"message_id": "5", "has_audio": True,
         "context_kind": "reply_reference_source", "reference_strength": "inherited_reference"},
        {"message_id": "6", "has_audio": True,
         "context_kind": "replied_message", "reference_strength": "explicit_reply"},
        {"message_id": "7", "has_audio": True,
         "context_kind": "reply_origin_request", "reference_strength": "prior_user_request"},
        {"message_id": "8", "has_audio": True,
         "context_kind": "reply_origin_source", "reference_strength": "prior_explicit_reply",
         "audio_explicit_reference": True},
        {"message_id": "9", "has_audio": True,
         "context_kind": "reply_origin_source", "reference_strength": "prior_explicit_reply",
         "audio_explicit_reference": False},
    ]
    refs = audio_context_refs(rows)
    assert {r.message_id for r in refs} == {"6", "7", "8"}


def test_turn_provenance_records_audio_without_bytes_and_only_marks_actual_reply():
    original = message(10, [Attachment(wav_bytes(1))])
    original.content = "이 노래 들려?"
    provenance = build_turn_provenance(original, original.content, [], [])
    assert provenance["origin_request"]["has_audio"] is True
    assert "audio_url" not in str(provenance)

    text_only = message(11)
    replied = [{
        "message_id": "10", "author_user_id": "10", "role": "user",
        "has_audio": True, "reference_strength": "explicit_reply",
    }]
    inherited = build_turn_provenance(text_only, "이게 뭐야?", replied, [])
    assert inherited["origin_sources"][0]["has_audio"] is True
    assert inherited["origin_sources"][0]["audio_explicit_reference"] is True

    source = dict(replied[0], reference_strength="passive_recent")
    unrelated = build_turn_provenance(text_only, "이게 뭐야?", [source], [])
    assert unrelated["origin_sources"][0]["audio_explicit_reference"] is False


def test_reply_to_assistant_recovers_audio_origin_but_ambient_stays_metadata_only():
    scope = Scope(1, 10, 10)
    recent = TargetAwareRecentMessages()
    first = message(10, [Attachment(wav_bytes(1))])
    provenance = build_turn_provenance(first, "이 소리 뭐야?", [], [])
    provenance_token = CURRENT_TURN_PROVENANCE.set(provenance)
    reply_token = REPLY_CONTEXT.set(())
    try:
        recent.add(scope, 10, "user", "이 소리 뭐야?", direct_trigger=True, has_audio=True)
        recent.add(
            scope, 11, "hina", "어떤 소리가 들리네.",
            role="assistant", author_user_id=99,
            reply_target_user_id=10, capture_turn_provenance=True,
        )
    finally:
        REPLY_CONTEXT.reset(reply_token)
        CURRENT_TURN_PROVENANCE.reset(provenance_token)

    recent.add(Scope(1, 10, 20), 12, "other", "내 음성",
               has_audio=True, direct_trigger=False)
    reply_token = REPLY_CONTEXT.set(({
        "message_id": "11", "user_id": "99", "author_user_id": "99",
        "role": "assistant", "name": "hina", "content": "어떤 소리가 들리네.",
        "context_kind": "replied_message", "reference_strength": "explicit_reply",
    },))
    try:
        rows = recent.context(scope, 13)
        origin = next(row for row in rows if row.get("context_kind") == "reply_origin_request")
        assert origin["message_id"] == "10"
        assert origin["has_audio"] is True
        assert [r.message_id for r in audio_context_refs(rows)] == ["10"]
        ambient = next(row for row in rows if row.get("message_id") == 12)
        assert ambient["has_audio"] is True
    finally:
        REPLY_CONTEXT.reset(reply_token)

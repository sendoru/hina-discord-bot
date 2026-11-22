import io
import wave
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from hina_bot.ai.audio import AudioInput, augment_audio_input
from hina_bot.ai.providers import _gemini_content, _gemini_generate_content_parts
from hina_bot.discord.audio import (
    AudioLimits,
    audio_context_refs,
    collect_audio_inputs,
    message_has_audio,
)


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
async def test_native_audio_current_and_bounded_recent():
    previous = message(10, [Attachment(wav_bytes(2))])
    current = message(11, [Attachment(wav_bytes(1))])
    async def fetch(mid):
        assert mid == 10
        return previous
    current.channel = SimpleNamespace(fetch_message=fetch)
    refs = audio_context_refs([{
        "message_id": "10", "context_kind": "channel_ambient",
        "reference_strength": "passive_recent", "has_audio": True,
    }])
    result = await collect_audio_inputs(
        current, context_refs=refs,
        limits=AudioLimits(attachments=2, recent_seconds=1),
    )
    assert message_has_audio(current)
    assert [entry.message_id for entry in result] == ["11"]
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
        limits=AudioLimits(attachments=2, recent_seconds=1),
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

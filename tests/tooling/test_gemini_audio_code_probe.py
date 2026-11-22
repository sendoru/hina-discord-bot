import wave

import pytest

from hina_bot.ai.providers import _gemini_generate_content_parts, _gemini_input
from hina_bot.tooling.gemini_audio_code_probe import (
    _input,
    _silence_wav,
    audio_sample,
    cases,
    main,
)


def test_default_probe_covers_both_apis_and_audio_code_controls():
    suite = cases()
    assert len(suite) == 6
    for api in ("interactions", "generate_content"):
        selected = [test for test in suite if test.api == api]
        assert [(case.variant, case.audio) for case in selected] == [
            ("text+code", False), ("audio-only", True), ("audio+code", True),
        ]
        assert not selected[1].tools
        assert selected[2].tools == ({"type": "code_execution"},)


def test_optional_function_and_search_probes_do_not_replace_code_probe():
    suite = cases(include_function=True, include_search=True)
    assert len(suite) == 10
    for api in ("interactions", "generate_content"):
        selected = {test.variant: test for test in suite if test.api == api}
        assert selected["audio+function"].tools[0]["type"] == "function"
        assert selected["audio+search"].tools == ({"type": "web_search"},)


def test_probe_audio_payload_matches_both_production_gemini_adapters():
    data, mime, _ = _silence_wav()
    case = next(test for test in cases() if test.variant == "audio+code")
    value = _input(case, data=data, mime=mime)
    steps = _gemini_input(value)
    assert len(steps) == 1
    assert steps[0]["type"] == "user_input"
    audio = next(part for part in steps[0]["content"] if part["type"] == "audio")
    assert audio["mime_type"] == mime
    parts = _gemini_generate_content_parts(value[0]["content"])
    assert any(part.get("inline_data", {}).get("mime_type") == mime for part in parts)


def test_probe_refuses_oversized_duration_and_unrecognized_media(tmp_path):
    path = tmp_path / "long.wav"
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(8000)
        wav.writeframes(b"\x00\x00" * 8000 * 11)
    with pytest.raises(ValueError, match="duration"):
        audio_sample(str(path), max_seconds=10)
    with pytest.raises(ValueError, match="container"):
        bad = tmp_path / "bad.ogg"
        bad.write_bytes(b"garbage")
        audio_sample(str(bad))


def test_dry_run_needs_no_keys_or_network(capsys, monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    assert main(["--dry-run"]) == 0
    text = capsys.readouterr().out
    assert "interactions | audio+code" in text
    assert "generate_content | audio+code" in text
    assert "text+code" in text

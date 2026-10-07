import json
from types import SimpleNamespace as NS

from hina_bot.ai.usage import ModelResponseError
from hina_bot.core.observability import (
    CURRENT_TURN_ID,
    EventLogger,
    safe_exception_fields,
)


def test_event_logger_correlates_turn_without_serializing_exception_message(tmp_path):
    path = tmp_path / "events.jsonl"
    logger = EventLogger(str(path))
    token = CURRENT_TURN_ID.set("opaque-turn-id")
    try:
        try:
            raise ValueError("secret user message and https://private.example")
        except ValueError as exc:
            fields = safe_exception_fields(exc, "generation")
            logger.emit(
                "turn.failed",
                level="error",
                stage="generation",
                **fields,
            )
    finally:
        CURRENT_TURN_ID.reset(token)
        logger.close()

    raw = path.read_text()
    assert "secret user message" not in raw
    assert "private.example" not in raw
    row = json.loads(raw)
    assert row["turn_id"] == "opaque-turn-id"
    assert row["event"] == "turn.failed"
    assert row["app_version"]
    assert row["build_revision"].startswith(("git:", "src:"))
    assert len(row["runtime_id"]) == 16
    assert row["error_type"] == "ValueError"
    assert row["error_location"].endswith(":test_event_logger_correlates_turn_without_serializing_exception_message")
    assert len(row["error_fingerprint"]) == 16


def test_safe_exception_fields_include_content_free_model_response_diagnostics():
    response = NS(
        status="completed",
        output=[NS(type="message")],
        _hina_error_codes=[],
        usage=NS(
            input_tokens=100,
            output_tokens=0,
            total_tokens=100,
            input_tokens_details=NS(cached_tokens=20),
            output_tokens_details=NS(reasoning_tokens=40),
        ),
    )

    try:
        raise ModelResponseError("gemini", response, has_visible_text=False)
    except ModelResponseError as exc:
        fields = safe_exception_fields(exc, "generation")

    assert fields["error_type"] == "ModelResponseError"
    assert fields["provider"] == "gemini"
    assert fields["provider_response_status"] == "completed"
    assert fields["provider_error_code"] == "EMPTY_RESPONSE"
    assert fields["provider_output_types"] == ["message"]
    assert fields["provider_has_visible_text"] is False
    assert fields["provider_input_tokens"] == 100
    assert fields["provider_output_tokens"] == 0
    assert fields["provider_cached_tokens"] == 20
    assert fields["provider_reasoning_tokens"] == 40


def test_disabled_event_log_does_not_create_a_file(tmp_path):
    logger = EventLogger("")
    logger.emit("turn.received", content_chars=10)
    logger.close()
    assert list(tmp_path.iterdir()) == []

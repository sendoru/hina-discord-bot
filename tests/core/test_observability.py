import json

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


def test_disabled_event_log_does_not_create_a_file(tmp_path):
    logger = EventLogger("")
    logger.emit("turn.received", content_chars=10)
    logger.close()
    assert list(tmp_path.iterdir()) == []

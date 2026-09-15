"""Content-free structured event telemetry for Discord request lifecycles."""

from __future__ import annotations

import hashlib
import json
import logging
import traceback
import uuid
from contextvars import ContextVar
from datetime import UTC, datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path

CURRENT_TURN_ID: ContextVar[str | None] = ContextVar("hina_turn_id", default=None)


def new_turn_id() -> str:
    """Return an opaque correlation ID that contains no Discord identifiers."""
    return uuid.uuid4().hex[:20]


def current_turn_id() -> str | None:
    return CURRENT_TURN_ID.get()


def _short_location(frame: traceback.FrameSummary) -> str:
    path = frame.filename.replace("\\", "/")
    marker = "/hina_bot/"
    if marker in path:
        path = "hina_bot/" + path.split(marker, 1)[1]
    else:
        path = Path(path).name
    return f"{path}:{frame.lineno}:{frame.name}"


def safe_exception_fields(exc: BaseException, stage: str) -> dict[str, object]:
    """Describe an exception without serializing its message, arguments, or locals."""
    frames = traceback.extract_tb(exc.__traceback__)
    internal = [frame for frame in frames if "/hina_bot/" in frame.filename.replace("\\", "/")]
    location = _short_location((internal or frames)[-1]) if frames else "unknown"
    error_type = type(exc).__name__
    fingerprint = hashlib.sha256(
        f"{stage}|{error_type}|{location}".encode()
    ).hexdigest()[:16]
    fields = {
        "error_type": error_type,
        "error_location": location,
        "error_fingerprint": fingerprint,
    }
    status_code = getattr(exc, "status_code", None)
    if not isinstance(status_code, int):
        status_code = getattr(exc, "status", None)
    if isinstance(status_code, int):
        fields["http_status"] = status_code
    cause = exc.__cause__ or exc.__context__
    if cause is not None:
        fields["error_cause_type"] = type(cause).__name__
    return fields


class EventLogger:
    """Write small, content-free lifecycle events to a rotating JSONL file."""

    def __init__(self, path: str):
        self.handler = self._handler(path)

    @staticmethod
    def _handler(path: str):
        if not path:
            return None
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        handler = RotatingFileHandler(
            path,
            maxBytes=5_000_000,
            backupCount=3,
            encoding="utf-8",
            delay=True,
        )
        handler.setFormatter(logging.Formatter("%(message)s"))
        return handler

    def emit(self, event: str, *, level: str = "info", **fields) -> None:
        if not self.handler:
            return
        row = {
            "at": datetime.now(UTC).isoformat(),
            "level": level,
            "event": event,
        }
        turn_id = current_turn_id()
        if turn_id:
            row["turn_id"] = turn_id
        row.update(fields)
        try:
            payload = json.dumps(row, ensure_ascii=False, separators=(",", ":"))
            self.handler.handle(logging.LogRecord(
                "hina.events", logging.INFO, "", 0, payload, (), None
            ))
        except (OSError, TypeError, ValueError):
            logging.getLogger("hina").warning("Event log write failed")

    def close(self) -> None:
        if self.handler:
            self.handler.close()


__all__ = [
    "CURRENT_TURN_ID",
    "EventLogger",
    "current_turn_id",
    "new_turn_id",
    "safe_exception_fields",
]

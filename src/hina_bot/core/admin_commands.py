from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any

_ACTION_RE = re.compile(r"[a-z][a-z0-9_.-]{1,63}")
_REQUEST_ID_RE = re.compile(r"[a-f0-9]{32}")
MAX_PAYLOAD_BYTES = 8192


@dataclass(frozen=True)
class AdminCommand:
    id: int
    request_id: str
    actor: str
    action: str
    target: str
    payload: dict[str, Any]


def ensure_admin_command_schema(db: sqlite3.Connection) -> None:
    db.executescript(
        """
        CREATE TABLE IF NOT EXISTS admin_commands (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            request_id TEXT NOT NULL UNIQUE,
            actor TEXT NOT NULL,
            action TEXT NOT NULL,
            target TEXT NOT NULL DEFAULT '',
            payload_json TEXT NOT NULL DEFAULT '{}',
            status TEXT NOT NULL DEFAULT 'pending'
                CHECK(status IN ('pending','running','succeeded','failed')),
            result_json TEXT NOT NULL DEFAULT '{}',
            error_type TEXT NOT NULL DEFAULT '',
            error_message TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            started_at TEXT,
            finished_at TEXT
        );
        CREATE INDEX IF NOT EXISTS admin_commands_status
            ON admin_commands(status, id);
        """
    )


def _validate_action(action: str) -> str:
    action = str(action).strip().lower()
    if not _ACTION_RE.fullmatch(action):
        raise ValueError("invalid admin action")
    return action


def _validate_request_id(request_id: str) -> str:
    request_id = str(request_id).strip().lower()
    if not _REQUEST_ID_RE.fullmatch(request_id):
        raise ValueError("invalid admin request id")
    return request_id


def _encode_payload(payload: dict[str, Any]) -> str:
    if not isinstance(payload, dict):
        raise TypeError("admin command payload must be an object")
    encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    if len(encoded.encode("utf-8")) > MAX_PAYLOAD_BYTES:
        raise ValueError("admin command payload is too large")
    return encoded


def enqueue_admin_command(
    database_path: str | Path,
    *,
    request_id: str,
    action: str,
    target: str = "",
    payload: dict[str, Any] | None = None,
    actor: str = "local-dashboard",
) -> tuple[int, bool]:
    """Append one command without granting the dashboard arbitrary DB write access.

    The production Store owns schema creation. This function intentionally fails if
    the bot has not created the queue table yet.
    """

    path = Path(database_path).expanduser().resolve(strict=True)
    request_id = _validate_request_id(request_id)
    action = _validate_action(action)
    target = str(target).strip()
    actor = str(actor).strip() or "local-dashboard"
    if len(target) > 200:
        raise ValueError("admin command target is too long")
    if len(actor) > 100:
        raise ValueError("admin command actor is too long")
    encoded = _encode_payload(payload or {})

    db = sqlite3.connect(path, timeout=5.0)
    db.row_factory = sqlite3.Row
    try:
        db.execute("PRAGMA busy_timeout=5000")
        if db.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='admin_commands'"
        ).fetchone() is None:
            raise RuntimeError(
                "admin command queue is unavailable; start the updated bot once first"
            )
        try:
            with db:
                cursor = db.execute(
                    """INSERT INTO admin_commands(
                           request_id,actor,action,target,payload_json,status
                       ) VALUES (?,?,?,?,?,'pending')""",
                    (request_id, actor, action, target, encoded),
                )
            return int(cursor.lastrowid), True
        except sqlite3.IntegrityError:
            row = db.execute(
                "SELECT id FROM admin_commands WHERE request_id=?",
                (request_id,),
            ).fetchone()
            if row is None:
                raise
            return int(row["id"]), False
    finally:
        db.close()


def mark_interrupted_admin_commands(db: sqlite3.Connection) -> int:
    """Close commands that were running when the bot process stopped.

    We do not automatically retry them because an action may have committed before
    the previous process died. Resubmitting is an explicit operator decision.
    """

    with db:
        cursor = db.execute(
            """UPDATE admin_commands
               SET status='failed',
                   payload_json='{}',
                   error_type='InterruptedAdminCommand',
                   error_message='bot process stopped while command was running',
                   finished_at=CURRENT_TIMESTAMP
               WHERE status='running'"""
        )
    return max(0, int(cursor.rowcount))


def claim_next_admin_command(db: sqlite3.Connection) -> AdminCommand | None:
    while True:
        row = db.execute(
            """SELECT id,request_id,actor,action,target,payload_json
               FROM admin_commands
               WHERE status='pending'
               ORDER BY id
               LIMIT 1"""
        ).fetchone()
        if row is None:
            return None
        with db:
            changed = db.execute(
                """UPDATE admin_commands
                   SET status='running',started_at=CURRENT_TIMESTAMP
                   WHERE id=? AND status='pending'""",
                (int(row["id"]),),
            ).rowcount
        if not changed:
            continue
        try:
            payload = json.loads(str(row["payload_json"] or "{}"))
        except json.JSONDecodeError as exc:
            finish_admin_command(
                db,
                int(row["id"]),
                error_type=type(exc).__name__,
                error_message="stored admin command payload is invalid",
            )
            continue
        if not isinstance(payload, dict):
            finish_admin_command(
                db,
                int(row["id"]),
                error_type="TypeError",
                error_message="stored admin command payload is not an object",
            )
            continue
        return AdminCommand(
            id=int(row["id"]),
            request_id=str(row["request_id"]),
            actor=str(row["actor"]),
            action=str(row["action"]),
            target=str(row["target"]),
            payload=payload,
        )


def finish_admin_command(
    db: sqlite3.Connection,
    command_id: int,
    *,
    result: dict[str, Any] | None = None,
    error_type: str = "",
    error_message: str = "",
) -> None:
    success = not error_type
    result_json = _encode_payload(result or {})
    error_type = str(error_type)[:100]
    error_message = str(error_message)[:500]
    with db:
        db.execute(
            """UPDATE admin_commands
               SET status=?,
                   payload_json='{}',
                   result_json=?,
                   error_type=?,
                   error_message=?,
                   finished_at=CURRENT_TIMESTAMP
               WHERE id=? AND status='running'""",
            (
                "succeeded" if success else "failed",
                result_json,
                error_type,
                error_message,
                int(command_id),
            ),
        )

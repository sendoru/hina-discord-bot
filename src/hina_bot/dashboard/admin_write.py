from __future__ import annotations

import secrets
from dataclasses import dataclass
from urllib.parse import parse_qs

from fastapi import HTTPException, Request

from hina_bot.core.admin_commands import enqueue_admin_command


@dataclass
class AdminCommandWriter:
    database_path: str
    enabled: bool
    csrf_token: str

    @classmethod
    def create(cls, database_path: str, *, enabled: bool) -> AdminCommandWriter:
        return cls(
            database_path=database_path,
            enabled=enabled,
            csrf_token=secrets.token_urlsafe(32),
        )

    async def form(self, request: Request) -> dict[str, str]:
        if not self.enabled:
            raise HTTPException(status_code=403, detail="dashboard writes are disabled")
        content_type = request.headers.get("content-type", "")
        if not content_type.startswith("application/x-www-form-urlencoded"):
            raise HTTPException(status_code=415, detail="form encoded request required")
        try:
            parsed = parse_qs(
                (await request.body()).decode("utf-8"),
                keep_blank_values=True,
                strict_parsing=False,
            )
        except UnicodeDecodeError as exc:
            raise HTTPException(status_code=400, detail="invalid form encoding") from exc
        values = {key: rows[-1] for key, rows in parsed.items() if rows}
        supplied = values.pop("_csrf", "")
        if not supplied or not secrets.compare_digest(supplied, self.csrf_token):
            raise HTTPException(status_code=403, detail="invalid csrf token")
        return values

    def enqueue(
        self,
        *,
        request_id: str,
        action: str,
        target: str = "",
        payload: dict[str, object] | None = None,
    ) -> tuple[int, bool]:
        if not self.enabled:
            raise HTTPException(status_code=403, detail="dashboard writes are disabled")
        try:
            return enqueue_admin_command(
                self.database_path,
                request_id=request_id,
                action=action,
                target=target,
                payload=payload or {},
            )
        except (RuntimeError, ValueError) as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

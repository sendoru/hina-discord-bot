"""Hot-reloadable runtime configuration backed by SQLite overrides."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from .config import Settings, parse_call_prefixes


@dataclass(frozen=True)
class RuntimeSettingSpec:
    attr: str
    env_name: str
    kind: str
    minimum: int | None = None
    maximum: int | None = None
    empty_allowed: bool = False


RUNTIME_SETTING_SPECS: dict[str, RuntimeSettingSpec] = {
    "call_prefixes": RuntimeSettingSpec("call_prefixes", "CALL_PREFIXES", "prefixes"),
    "dm_always_reply": RuntimeSettingSpec("dm_always_reply", "DM_ALWAYS_REPLY", "bool"),
    "public_memory_in_dm": RuntimeSettingSpec(
        "public_memory_in_dm", "PUBLIC_SERVER_MEMORY_IN_DM", "bool"
    ),
    "chat_web_search": RuntimeSettingSpec("chat_web_search", "CHAT_WEB_SEARCH", "bool"),
    "community_lore": RuntimeSettingSpec("community_lore", "COMMUNITY_LORE", "bool"),
    "output_tokens": RuntimeSettingSpec(
        "output_tokens", "MAX_OUTPUT_TOKENS", "int", minimum=128, maximum=4096
    ),
    "channel_context_chars": RuntimeSettingSpec(
        "channel_context_chars", "CHANNEL_CONTEXT_CHARS", "int", minimum=0, maximum=12000
    ),
    "history_max_chars": RuntimeSettingSpec(
        "history_max_chars", "HISTORY_MAX_CHARS", "int", minimum=0, maximum=120000
    ),
    "lore_max_items": RuntimeSettingSpec(
        "lore_max_items", "LORE_MAX_ITEMS", "int", minimum=0, maximum=20
    ),
    "lore_max_chars": RuntimeSettingSpec(
        "lore_max_chars", "LORE_MAX_CHARS", "int", minimum=0, maximum=12000
    ),
    "runtime_default_location": RuntimeSettingSpec(
        "runtime_default_location",
        "RUNTIME_DEFAULT_LOCATION",
        "string",
        maximum=100,
        empty_allowed=True,
    ),
}

ENV_TO_RUNTIME_ATTR = {spec.env_name: attr for attr, spec in RUNTIME_SETTING_SPECS.items()}

_TRUE = {"1", "true", "yes", "on", "enable", "enabled"}
_FALSE = {"0", "false", "no", "off", "disable", "disabled"}


def runtime_setting_attr(key: str) -> str:
    normalized = key.strip()
    if normalized in RUNTIME_SETTING_SPECS:
        return normalized
    upper = normalized.upper()
    if upper in ENV_TO_RUNTIME_ATTR:
        return ENV_TO_RUNTIME_ATTR[upper]
    raise ValueError(f"알 수 없는 런타임 설정이에요: {key}")


def parse_runtime_value(spec: RuntimeSettingSpec, raw: str, *, settings=None) -> Any:
    text = raw.strip()
    if spec.kind == "bool":
        lowered = text.lower()
        if lowered in _TRUE:
            return True
        if lowered in _FALSE:
            return False
        raise ValueError("불리언 값은 on/off 또는 true/false로 입력해 주세요.")

    if spec.kind == "int":
        try:
            value = int(text)
        except ValueError as exc:
            raise ValueError("정수 값을 입력해 주세요.") from exc
        if spec.minimum is not None and value < spec.minimum:
            raise ValueError(f"{spec.env_name}는 {spec.minimum} 이상이어야 해요.")
        if spec.maximum is not None and value > spec.maximum:
            raise ValueError(f"{spec.env_name}는 {spec.maximum} 이하여야 해요.")
        if (
            spec.attr == "output_tokens"
            and settings is not None
            and getattr(settings, "provider", "") == "gemini"
            and value > getattr(settings, "gemini_total_output_tokens", value)
        ):
            raise ValueError(
                "Gemini에서는 MAX_OUTPUT_TOKENS가 GEMINI_TOTAL_OUTPUT_TOKENS보다 클 수 없어요."
            )
        return value

    if spec.kind == "prefixes":
        return parse_call_prefixes(text)

    if spec.kind == "string":
        if spec.empty_allowed and text.lower() in {"none", "null", "off", "-"}:
            text = ""
        if not text and not spec.empty_allowed:
            raise ValueError("빈 값은 사용할 수 없어요.")
        if spec.maximum is not None and len(text) > spec.maximum:
            raise ValueError(f"{spec.env_name}는 {spec.maximum}자 이하여야 해요.")
        if any(char in text for char in "\r\n\0"):
            raise ValueError("줄바꿈이나 NUL 문자는 사용할 수 없어요.")
        return text

    raise ValueError(f"지원하지 않는 설정 형식이에요: {spec.kind}")


def encode_runtime_value(value: Any) -> str:
    if isinstance(value, tuple):
        value = list(value)
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def decode_runtime_value(spec: RuntimeSettingSpec, encoded: str, *, settings=None) -> Any:
    value = json.loads(encoded)
    if spec.kind == "bool":
        if type(value) is not bool:
            raise ValueError("stored value is not boolean")
        return value
    if spec.kind == "int":
        if type(value) is not int:
            raise ValueError("stored value is not integer")
        return parse_runtime_value(spec, str(value), settings=settings)
    if spec.kind == "prefixes":
        if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
            raise ValueError("stored value is not a prefix list")
        return parse_call_prefixes(",".join(value))
    if spec.kind == "string":
        if not isinstance(value, str):
            raise ValueError("stored value is not string")
        return parse_runtime_value(spec, value or "none", settings=settings)
    raise ValueError(f"unsupported runtime setting kind: {spec.kind}")


def format_runtime_value(value: Any) -> str:
    if isinstance(value, tuple):
        return ", ".join(value)
    if isinstance(value, bool):
        return "on" if value else "off"
    if value == "":
        return "(비움)"
    return str(value)


class RuntimeSettings:
    """Read-through Settings facade with persistent SQLite overrides.

    Precedence is: SQLite runtime override > startup Settings (.env/.env.local) > code default.
    Settings.load() already resolves the latter two layers before this facade is created.
    """

    def __init__(self, base: Settings, store):
        object.__setattr__(self, "_base", base)
        object.__setattr__(self, "_store", store)
        object.__setattr__(self, "_overrides", {})
        store.db.execute(
            """CREATE TABLE IF NOT EXISTS runtime_config (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )"""
        )
        store.db.commit()
        self.reload()

    @property
    def base(self) -> Settings:
        return self._base

    def _stored(self) -> dict[str, str]:
        rows = self._store.db.execute(
            "SELECT key,value FROM runtime_config ORDER BY key"
        ).fetchall()
        return {str(row["key"]): str(row["value"]) for row in rows}

    def reload(self) -> None:
        loaded: dict[str, Any] = {}
        for attr, encoded in self._stored().items():
            spec = RUNTIME_SETTING_SPECS.get(attr)
            if spec is None:
                continue
            try:
                loaded[attr] = decode_runtime_value(spec, encoded, settings=self._base)
            except (TypeError, ValueError, json.JSONDecodeError):
                # Fail closed to the startup value if the DB contains an old or malformed value.
                continue
        object.__setattr__(self, "_overrides", loaded)

    def __getattr__(self, name: str):
        overrides = object.__getattribute__(self, "_overrides")
        if name in overrides:
            return overrides[name]
        return getattr(object.__getattribute__(self, "_base"), name)

    def base_value(self, key: str):
        attr = runtime_setting_attr(key)
        return getattr(self._base, attr)

    def override_value(self, key: str):
        attr = runtime_setting_attr(key)
        return self._overrides.get(attr)

    def source(self, key: str) -> str:
        attr = runtime_setting_attr(key)
        return "db" if attr in self._overrides else "startup"

    def _write(self, attr: str, encoded: str | None) -> None:
        with self._store.db:
            if encoded is None:
                self._store.db.execute("DELETE FROM runtime_config WHERE key=?", (attr,))
            else:
                self._store.db.execute(
                    """INSERT INTO runtime_config(key,value,updated_at)
                       VALUES (?,?,CURRENT_TIMESTAMP)
                       ON CONFLICT(key) DO UPDATE SET
                           value=excluded.value, updated_at=CURRENT_TIMESTAMP""",
                    (attr, encoded),
                )

    def set_text(self, key: str, raw: str):
        attr = runtime_setting_attr(key)
        spec = RUNTIME_SETTING_SPECS[attr]
        value = parse_runtime_value(spec, raw, settings=self)
        self._write(attr, encode_runtime_value(value))
        self._overrides[attr] = value
        return value

    def reset(self, key: str):
        attr = runtime_setting_attr(key)
        self._write(attr, None)
        self._overrides.pop(attr, None)
        return getattr(self._base, attr)

    def rows(self) -> list[tuple[RuntimeSettingSpec, Any, str]]:
        return [
            (spec, getattr(self, attr), self.source(attr))
            for attr, spec in RUNTIME_SETTING_SPECS.items()
        ]

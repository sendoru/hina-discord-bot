"""Hot-reloadable runtime configuration backed by SQLite overrides."""

from __future__ import annotations

import json
from dataclasses import dataclass
from math import isfinite
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .config import (
    EXTERNAL_CONTEXT_POLICIES,
    GEMINI_THINKING_LEVELS,
    MODEL_ROUTING_MODES,
    RETRIEVAL_V2_MODES,
    Settings,
    parse_call_prefixes,
    parse_discord_id_set,
    parse_external_context_policy,
)


@dataclass(frozen=True)
class RuntimeSettingSpec:
    attr: str
    env_name: str
    kind: str
    description: str = ""
    minimum: int | float | None = None
    maximum: int | float | None = None
    empty_allowed: bool = False
    choices: tuple[str, ...] = ()


_RUNTIME_SETTING_DESCRIPTIONS: dict[str, str] = {
    "call_prefixes": "메시지 시작에서 히나 호출로 인식할 접두어 목록입니다. 순서가 유지되며 각 접두어는 최대 32자입니다.",
    "dm_always_reply": "DM에서 호출어·멘션 없이 보낸 일반 메시지에도 항상 답할지 정합니다.",
    "always_reply_channel_ids": "호출어 없이도 사람의 일반 메시지에 답하는 서버 채널 ID 목록입니다.",
    "public_memory_in_dm": "공개 서버에서 같은 사용자와 나눈 대화를 DM 답변에서 추가 참고할지 정합니다.",
    "external_context_policy": "외부 LLM provider로 보낼 수 있는 대화 문맥의 최종 프라이버시 경계를 정합니다.",
    "chat_web_search": "일반 답변에서 provider의 웹 검색 기능을 fallback으로 허용할지 정합니다.",
    "retrieval_v2_mode": "Retrieval v2 rollout 모드입니다. off는 legacy만, shadow는 legacy 답변과 비동기 비교, active는 v2 context를 사용하고 실패 시 legacy로 즉시 fallback합니다.",
    "retrieval_v2_timeout_seconds": "Retrieval v2 shadow/active 한 턴의 전체 실행 시간 상한(초)입니다.",
    "community_lore": "community_meme 분류의 lore 항목을 런타임에서 사용할지 정합니다.",
    "model_routing_mode": "fixed 모델 하나를 쓸지, 요청 난이도에 따라 fast/smart tier를 고르는 adaptive routing을 사용할지 정합니다.",
    "model": "fixed routing에서 사용할 기본 LLM 모델 이름입니다.",
    "fast_model": "adaptive routing의 fast tier에서 사용할 모델 이름입니다.",
    "smart_model": "adaptive routing의 smart tier에서 사용할 모델 이름입니다.",
    "output_tokens": "fixed routing에서 한 요청에 허용할 provider 공통 최대 출력 토큰 예산입니다.",
    "fast_output_tokens": "adaptive fast tier의 최대 출력 토큰 예산입니다. SMART_MAX_OUTPUT_TOKENS보다 클 수 없습니다.",
    "smart_output_tokens": "adaptive smart tier와 명시적 장문 요청에 사용할 최대 출력 토큰 예산입니다.",
    "memory_output_tokens": "장기 기억 요약·추출 요청에 사용할 최대 출력 토큰 예산입니다.",
    "routing_classifier_max_output_tokens": "semantic/web routing classifier 응답에 허용할 최대 출력 토큰 예산입니다.",
    "gemini_thinking_level": "Gemini fixed routing에서 사용할 추론 강도입니다.",
    "gemini_fast_thinking_level": "Gemini adaptive fast tier에서 사용할 추론 강도입니다.",
    "gemini_smart_thinking_level": "Gemini adaptive smart tier에서 사용할 추론 강도입니다.",
    "gemini_store_interactions": "일반 채팅 answer의 Gemini interaction을 provider 측 로그에 저장할지 정합니다.",
    "gemini_store_classifier_interactions": "semantic/web routing classifier의 Gemini interaction을 provider 측 로그에 저장할지 별도로 정합니다.",
    "model_routing_smart_threshold": "채팅 routing score가 이 값 이상일 때 smart tier를 선택합니다.",
    "memory_routing_smart_threshold": "장기 기억 routing score가 이 값 이상일 때 smart tier를 선택합니다.",
    "channel_context_chars": "최근 서버 채널 문맥에서 외부 모델 요청에 사용할 본문 문자 수 상한입니다. 0이면 전달하지 않습니다.",
    "history_max_chars": "DM 최근 대화에서 외부 모델 요청에 사용할 본문 문자 수 상한입니다.",
    "cooldown": "같은 사용자의 연속 호출을 제한하는 cooldown 시간(초)입니다.",
    "summary_every": "기존 text summary를 갱신하기 전에 필요한 새 대화 턴 수입니다. HISTORY_TURNS 이하여야 합니다.",
    "structured_memory_every": "structured memory extractor가 한 번에 처리할 새 대화 턴 수입니다. HISTORY_TURNS 이하여야 합니다.",
    "structured_memory_stale_after_seconds": "처리되지 않은 structured memory tail을 background sweep 대상으로 볼 때까지 기다리는 시간(초)입니다.",
    "lore_max_items": "한 요청에 retrieval하여 전달할 lore 항목 수의 상한입니다.",
    "lore_max_chars": "한 요청에 retrieval하여 전달할 lore 본문 총 문자 수의 상한입니다.",
    "runtime_timezone": "현재 시각과 상대 날짜 해석, ambient context에 사용할 IANA timezone입니다.",
    "runtime_locale": "ambient context와 외부 정보 요청에 사용할 locale 식별자입니다.",
    "runtime_default_location": "사용자가 지역을 생략했을 때만 날씨·교통·영업시간 등에 쓰는 fallback 위치입니다. 실제 사용자 위치로 간주하지 않습니다.",
    "empty_call_reply": "호출어·멘션만 있고 본문이 비어 있을 때 보내는 고정 응답입니다.",
    "special_dm_empty_call_reply": "SPECIAL_DM_USER_ID와의 DM에서 빈 호출에만 사용하는 고정 응답입니다. 비우면 EMPTY_CALL_REPLY를 사용합니다.",
    "empty_response_reply": "모델이 빈 문자열을 반환했을 때 Discord에 보내는 최종 fallback 응답입니다.",
}


def _runtime_spec(
    attr: str,
    env_name: str,
    kind: str,
    **kwargs,
) -> RuntimeSettingSpec:
    return RuntimeSettingSpec(
        attr,
        env_name,
        kind,
        description=_RUNTIME_SETTING_DESCRIPTIONS[attr],
        **kwargs,
    )


RUNTIME_SETTING_SPECS: dict[str, RuntimeSettingSpec] = {
    "call_prefixes": _runtime_spec(
        "call_prefixes", "CALL_PREFIXES", "prefixes", maximum=20
    ),
    "dm_always_reply": _runtime_spec("dm_always_reply", "DM_ALWAYS_REPLY", "bool"),
    "always_reply_channel_ids": _runtime_spec(
        "always_reply_channel_ids",
        "ALWAYS_REPLY_CHANNEL_IDS",
        "discord_ids",
        maximum=100,
        empty_allowed=True,
    ),
    "public_memory_in_dm": _runtime_spec(
        "public_memory_in_dm", "PUBLIC_SERVER_MEMORY_IN_DM", "bool"
    ),
    "external_context_policy": _runtime_spec(
        "external_context_policy",
        "EXTERNAL_CONTEXT_POLICY",
        "string",
        choices=tuple(sorted(EXTERNAL_CONTEXT_POLICIES)),
    ),
    "chat_web_search": _runtime_spec("chat_web_search", "CHAT_WEB_SEARCH", "bool"),
    "retrieval_v2_mode": _runtime_spec(
        "retrieval_v2_mode",
        "RETRIEVAL_V2_MODE",
        "string",
        choices=tuple(sorted(RETRIEVAL_V2_MODES)),
    ),
    "retrieval_v2_timeout_seconds": _runtime_spec(
        "retrieval_v2_timeout_seconds",
        "RETRIEVAL_V2_TIMEOUT_SECONDS",
        "float",
        minimum=0.25,
        maximum=30.0,
    ),
    "community_lore": _runtime_spec("community_lore", "COMMUNITY_LORE", "bool"),
    "model_routing_mode": _runtime_spec(
        "model_routing_mode",
        "MODEL_ROUTING_MODE",
        "string",
        choices=tuple(sorted(MODEL_ROUTING_MODES)),
    ),
    "model": _runtime_spec("model", "LLM_MODEL", "string", maximum=200),
    "fast_model": _runtime_spec("fast_model", "LLM_FAST_MODEL", "string", maximum=200),
    "smart_model": _runtime_spec("smart_model", "LLM_SMART_MODEL", "string", maximum=200),
    "output_tokens": _runtime_spec(
        "output_tokens", "MAX_OUTPUT_TOKENS", "int", minimum=128, maximum=65536
    ),
    "fast_output_tokens": _runtime_spec(
        "fast_output_tokens", "FAST_MAX_OUTPUT_TOKENS", "int", minimum=128, maximum=65536
    ),
    "smart_output_tokens": _runtime_spec(
        "smart_output_tokens", "SMART_MAX_OUTPUT_TOKENS", "int", minimum=128, maximum=65536
    ),
    "memory_output_tokens": _runtime_spec(
        "memory_output_tokens", "MEMORY_MAX_OUTPUT_TOKENS", "int", minimum=128, maximum=65536
    ),
    "routing_classifier_max_output_tokens": _runtime_spec(
        "routing_classifier_max_output_tokens",
        "ROUTING_CLASSIFIER_MAX_OUTPUT_TOKENS",
        "int",
        minimum=32,
        maximum=1024,
    ),
    "gemini_thinking_level": _runtime_spec(
        "gemini_thinking_level",
        "GEMINI_THINKING_LEVEL",
        "gemini_thinking_level",
        choices=tuple(sorted(GEMINI_THINKING_LEVELS)),
    ),
    "gemini_fast_thinking_level": _runtime_spec(
        "gemini_fast_thinking_level",
        "GEMINI_FAST_THINKING_LEVEL",
        "gemini_thinking_level",
        choices=tuple(sorted(GEMINI_THINKING_LEVELS)),
    ),
    "gemini_smart_thinking_level": _runtime_spec(
        "gemini_smart_thinking_level",
        "GEMINI_SMART_THINKING_LEVEL",
        "gemini_thinking_level",
        choices=tuple(sorted(GEMINI_THINKING_LEVELS)),
    ),
    "gemini_store_interactions": _runtime_spec(
        "gemini_store_interactions", "GEMINI_STORE_INTERACTIONS", "bool"
    ),
    "gemini_store_classifier_interactions": _runtime_spec(
        "gemini_store_classifier_interactions",
        "GEMINI_STORE_CLASSIFIER_INTERACTIONS",
        "bool",
    ),
    "model_routing_smart_threshold": _runtime_spec(
        "model_routing_smart_threshold",
        "MODEL_ROUTING_SMART_THRESHOLD",
        "float",
        minimum=0.1,
        maximum=10.0,
    ),
    "memory_routing_smart_threshold": _runtime_spec(
        "memory_routing_smart_threshold",
        "MEMORY_ROUTING_SMART_THRESHOLD",
        "float",
        minimum=0.1,
        maximum=10.0,
    ),
    "channel_context_chars": _runtime_spec(
        "channel_context_chars", "CHANNEL_CONTEXT_CHARS", "int", minimum=0, maximum=12000
    ),
    "history_max_chars": _runtime_spec(
        "history_max_chars", "HISTORY_MAX_CHARS", "int", minimum=0, maximum=120000
    ),
    "cooldown": _runtime_spec(
        "cooldown", "COOLDOWN_SECONDS", "float", minimum=0.0, maximum=3600.0
    ),
    "summary_every": _runtime_spec(
        "summary_every", "SUMMARY_EVERY", "int", minimum=2, maximum=30
    ),
    "structured_memory_every": _runtime_spec(
        "structured_memory_every", "STRUCTURED_MEMORY_EVERY", "int", minimum=2, maximum=30
    ),
    "structured_memory_stale_after_seconds": _runtime_spec(
        "structured_memory_stale_after_seconds",
        "STRUCTURED_MEMORY_STALE_AFTER_SECONDS",
        "int",
        minimum=60,
        maximum=7 * 24 * 60 * 60,
    ),
    "lore_max_items": _runtime_spec(
        "lore_max_items", "LORE_MAX_ITEMS", "int", minimum=0, maximum=20
    ),
    "lore_max_chars": _runtime_spec(
        "lore_max_chars", "LORE_MAX_CHARS", "int", minimum=0, maximum=12000
    ),
    "runtime_timezone": _runtime_spec(
        "runtime_timezone", "RUNTIME_TIMEZONE", "string", maximum=100
    ),
    "runtime_locale": _runtime_spec(
        "runtime_locale", "RUNTIME_LOCALE", "string", maximum=32
    ),
    "runtime_default_location": _runtime_spec(
        "runtime_default_location",
        "RUNTIME_DEFAULT_LOCATION",
        "string",
        maximum=100,
        empty_allowed=True,
    ),
    "empty_call_reply": _runtime_spec(
        "empty_call_reply", "EMPTY_CALL_REPLY", "string", maximum=200
    ),
    "special_dm_empty_call_reply": _runtime_spec(
        "special_dm_empty_call_reply",
        "SPECIAL_DM_EMPTY_CALL_REPLY",
        "string",
        maximum=200,
        empty_allowed=True,
    ),
    "empty_response_reply": _runtime_spec(
        "empty_response_reply", "EMPTY_RESPONSE_REPLY", "string", maximum=200
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
        return value

    if spec.kind == "float":
        try:
            value = float(text)
        except ValueError as exc:
            raise ValueError("숫자 값을 입력해 주세요.") from exc
        if not isfinite(value):
            raise ValueError("유한한 숫자 값을 입력해 주세요.")
        if spec.minimum is not None and value < spec.minimum:
            raise ValueError(f"{spec.env_name}는 {spec.minimum} 이상이어야 해요.")
        if spec.maximum is not None and value > spec.maximum:
            raise ValueError(f"{spec.env_name}는 {spec.maximum} 이하여야 해요.")
        return value

    if spec.kind == "gemini_thinking_level":
        level = text.lower()
        if level not in GEMINI_THINKING_LEVELS:
            allowed = ", ".join(sorted(GEMINI_THINKING_LEVELS))
            raise ValueError(f"{spec.env_name}은 {allowed} 중 하나여야 해요.")
        return level

    if spec.kind == "prefixes":
        return parse_call_prefixes(text)

    if spec.kind == "discord_ids":
        return parse_discord_id_set(text, spec.env_name)

    if spec.kind == "string":
        if spec.attr == "external_context_policy":
            return parse_external_context_policy(text)
        if spec.empty_allowed and text.lower() in {"none", "null", "off", "-"}:
            text = ""
        if not text and not spec.empty_allowed:
            raise ValueError("빈 값은 사용할 수 없어요.")
        if spec.maximum is not None and len(text) > spec.maximum:
            raise ValueError(f"{spec.env_name}는 {spec.maximum}자 이하여야 해요.")
        if any(char in text for char in "\r\n\0"):
            raise ValueError("줄바꿈이나 NUL 문자는 사용할 수 없어요.")
        if spec.choices and text not in spec.choices:
            allowed = ", ".join(spec.choices)
            raise ValueError(f"{spec.env_name}는 {allowed} 중 하나여야 해요.")
        if spec.attr == "runtime_timezone":
            try:
                ZoneInfo(text)
            except ZoneInfoNotFoundError as exc:
                raise ValueError(f"알 수 없는 RUNTIME_TIMEZONE이에요: {text}") from exc
        return text

    raise ValueError(f"지원하지 않는 설정 형식이에요: {spec.kind}")


def encode_runtime_value(value: Any) -> str:
    if isinstance(value, tuple):
        value = list(value)
    elif isinstance(value, (set, frozenset)):
        value = sorted(value)
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
    if spec.kind == "float":
        if type(value) not in {int, float}:
            raise ValueError("stored value is not numeric")
        return parse_runtime_value(spec, str(value), settings=settings)
    if spec.kind == "gemini_thinking_level":
        if not isinstance(value, str):
            raise ValueError("stored value is not a Gemini thinking level")
        return parse_runtime_value(spec, value, settings=settings)
    if spec.kind == "prefixes":
        if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
            raise ValueError("stored value is not a prefix list")
        return parse_call_prefixes(",".join(value))
    if spec.kind == "discord_ids":
        if not isinstance(value, list) or any(type(item) is not int for item in value):
            raise ValueError("stored value is not a Discord ID list")
        return parse_discord_id_set(",".join(str(item) for item in value), spec.env_name)
    if spec.kind == "string":
        if not isinstance(value, str):
            raise ValueError("stored value is not string")
        return parse_runtime_value(spec, value or "none", settings=settings)
    raise ValueError(f"unsupported runtime setting kind: {spec.kind}")


def format_runtime_value(value: Any) -> str:
    if isinstance(value, tuple):
        return ", ".join(value)
    if isinstance(value, (set, frozenset)):
        return ", ".join(str(item) for item in sorted(value)) or "(비움)"
    if isinstance(value, bool):
        return "on" if value else "off"
    if value == "":
        return "(비움)"
    return str(value)


def _validate_combined_runtime_value(settings, attr: str, value: Any) -> None:
    effective = {
        "fast_output_tokens": value if attr == "fast_output_tokens" else settings.fast_output_tokens,
        "smart_output_tokens": value if attr == "smart_output_tokens" else settings.smart_output_tokens,
        "summary_every": value if attr == "summary_every" else settings.summary_every,
        "structured_memory_every": (
            value if attr == "structured_memory_every" else settings.structured_memory_every
        ),
    }
    if effective["fast_output_tokens"] > effective["smart_output_tokens"]:
        raise ValueError("FAST_MAX_OUTPUT_TOKENS는 SMART_MAX_OUTPUT_TOKENS 이하여야 해요.")
    history_turns = settings.history_turns
    if effective["summary_every"] > history_turns:
        raise ValueError("SUMMARY_EVERY는 HISTORY_TURNS 이하여야 해요.")
    if effective["structured_memory_every"] > history_turns:
        raise ValueError("STRUCTURED_MEMORY_EVERY는 HISTORY_TURNS 이하여야 해요.")




class RuntimeSettings:
    """Read-through Settings facade with persistent SQLite overrides.

    Precedence is: SQLite runtime override > startup Settings (.env/.env.local) > code default.
    Settings.load() already resolves the latter two layers before this facade is created.
    """

    def __init__(self, base: Settings, store):
        object.__setattr__(self, "_base", base)
        object.__setattr__(self, "_store", store)
        object.__setattr__(self, "_overrides", {})
        store.db.executescript(
            """
            CREATE TABLE IF NOT EXISTS runtime_config (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS runtime_config_startup (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL,
                captured_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            """
        )
        self._capture_startup_snapshot()
        self.reload()

    def _capture_startup_snapshot(self) -> None:
        rows = [
            (attr, encode_runtime_value(getattr(self._base, attr)))
            for attr in RUNTIME_SETTING_SPECS
        ]
        with self._store.db:
            self._store.db.execute("DELETE FROM runtime_config_startup")
            self._store.db.executemany(
                """INSERT INTO runtime_config_startup(key,value,captured_at)
                   VALUES (?,?,CURRENT_TIMESTAMP)""",
                rows,
            )

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
        _validate_combined_runtime_value(self, attr, value)
        self._write(attr, encode_runtime_value(value))
        self._overrides[attr] = value
        return value

    def reset(self, key: str):
        attr = runtime_setting_attr(key)
        value = getattr(self._base, attr)
        _validate_combined_runtime_value(self, attr, value)
        self._write(attr, None)
        self._overrides.pop(attr, None)
        return value

    def rows(self) -> list[tuple[RuntimeSettingSpec, Any, str]]:
        return [
            (spec, getattr(self, attr), self.source(attr))
            for attr, spec in RUNTIME_SETTING_SPECS.items()
        ]

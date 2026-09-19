import math
import os
from dataclasses import dataclass
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from dotenv import load_dotenv

SUPPORTED_MODEL_PROVIDERS = frozenset({"openai", "gemini", "openrouter"})
GEMINI_THINKING_LEVELS = frozenset({"minimal", "low", "medium", "high"})
EXTERNAL_CONTEXT_POLICIES = frozenset({"full", "bot_interactions_only"})
MODEL_ROUTING_MODES = frozenset({"fixed", "adaptive"})
ROUTING_CLASSIFIER_MODES = frozenset({"off", "shadow", "active"})


def parse_call_prefixes(value: str) -> tuple[str, ...]:
    """Parse a comma-separated, ordered set of message prefixes."""
    prefixes = tuple(dict.fromkeys(part.strip() for part in value.split(",") if part.strip()))
    if not prefixes or len(prefixes) > 20:
        raise ValueError("CALL_PREFIXES는 쉼표로 구분한 1~20개의 접두어여야 합니다.")
    if any(len(prefix) > 32 or any(char in prefix for char in "\r\n\0")
           for prefix in prefixes):
        raise ValueError("각 호출 접두어는 줄바꿈 없이 1~32자여야 합니다.")
    return prefixes


def _provider(value: str, variable: str) -> str:
    provider = value.strip().lower()
    if provider not in SUPPORTED_MODEL_PROVIDERS:
        allowed = ", ".join(sorted(SUPPORTED_MODEL_PROVIDERS))
        raise ValueError(f"{variable}는 {allowed} 중 하나여야 합니다.")
    return provider


def parse_external_context_policy(value: str) -> str:
    policy = value.strip().lower()
    if policy not in EXTERNAL_CONTEXT_POLICIES:
        allowed = ", ".join(sorted(EXTERNAL_CONTEXT_POLICIES))
        raise ValueError(f"EXTERNAL_CONTEXT_POLICY는 {allowed} 중 하나여야 합니다.")
    return policy


def _env_key(provider: str) -> str:
    return {
        "openai": "OPENAI_API_KEY",
        "gemini": "GEMINI_API_KEY",
        "openrouter": "OPENROUTER_API_KEY",
    }[provider]


@dataclass(frozen=True)
class Settings:
    # api_key is retained for backwards-compatible direct construction in tests/scripts.
    # Runtime-loaded settings also keep provider-specific keys below.
    api_key: str
    discord_token: str
    model: str = "gpt-4.1-mini"
    model_routing_mode: str = "fixed"
    model_routing_smart_threshold: float = 2.0
    memory_routing_smart_threshold: float = 2.0
    fast_model: str = "gpt-4.1-mini"
    smart_model: str = "gpt-4.1-mini"
    fast_output_tokens: int = 4096
    smart_output_tokens: int = 8192
    memory_output_tokens: int = 4096
    routing_classifier_mode: str = "off"
    routing_classifier_provider: str = "openai"
    routing_classifier_model: str = "gpt-4.1-mini"
    routing_classifier_api_key: str = ""
    routing_classifier_timeout_seconds: float = 4.0
    routing_classifier_max_output_tokens: int = 256
    provider: str = "openai"
    openai_api_key: str = ""
    gemini_api_key: str = ""
    openrouter_api_key: str = ""
    gemini_thinking_level: str = "low"
    gemini_fast_thinking_level: str = "minimal"
    gemini_smart_thinking_level: str = "medium"
    db_path: str = "data/hina.sqlite3"
    prompt_path: str = ""
    call_prefixes: tuple[str, ...] = ("히나야",)
    empty_call_reply: str = "무슨 일이야?"
    special_dm_empty_call_reply: str = ""
    empty_response_reply: str = "..."
    dm_always_reply: bool = False
    public_memory_in_dm: bool = True
    external_context_policy: str = "bot_interactions_only"
    allowed_guild_ids: frozenset[int] = frozenset()
    cooldown: float = 5
    concurrency: int = 3
    output_tokens: int = 1000
    summary_every: int = 8
    structured_memory_every: int = 4
    history_turns: int = 12
    history_max_chars: int = 12000
    usage_log_path: str = "data/logs/usage.jsonl"
    event_log_path: str = "data/logs/events.jsonl"
    channel_context_chars: int = 6000
    special_dm_user_id: int | None = None
    bot_admin_ids: frozenset[int] = frozenset()
    lore_path: str = ""
    lore_max_items: int = 6
    lore_max_chars: int = 3200
    community_lore: bool = True
    chat_web_search: bool = True
    runtime_timezone: str = "Asia/Seoul"
    runtime_locale: str = "ko-KR"
    runtime_default_location: str = ""
    vision_max_attachments: int = 4
    vision_max_emojis: int = 12
    vision_max_stickers: int = 8

    def api_key_for(self, provider: str) -> str:
        provider = _provider(provider, "provider")
        explicit = {
            "openai": self.openai_api_key,
            "gemini": self.gemini_api_key,
            "openrouter": self.openrouter_api_key,
        }[provider].strip()
        if explicit:
            return explicit
        # Legacy Settings("key", "token") means an OpenAI configuration. For manually
        # constructed non-OpenAI settings, api_key is the selected primary provider key.
        if provider == self.provider or (provider == "openai" and self.provider == "openai"):
            return self.api_key.strip()
        return ""

    def routing_classifier_key(self) -> str:
        """Use a dedicated classifier credential when configured, otherwise the provider key."""
        return self.routing_classifier_api_key.strip() or self.api_key_for(
            self.routing_classifier_provider
        )

    @classmethod
    def load(cls):
        load_dotenv(Path.cwd() / ".env.local", override=False)
        load_dotenv(Path.cwd() / ".env", override=False)

        token = os.getenv("DISCORD_TOKEN", "").strip()
        if not token:
            raise ValueError(".env.local에 DISCORD_TOKEN을 설정해 주세요.")

        provider = _provider(os.getenv("LLM_PROVIDER", "openai"), "LLM_PROVIDER")

        # OPENAI_MODEL remains a backwards-compatible alias for existing deployments.
        model = (os.getenv("LLM_MODEL", "").strip()
                 or os.getenv("OPENAI_MODEL", "").strip()
                 or ("gpt-4.1-mini" if provider == "openai" else ""))
        if not model:
            raise ValueError("LLM_MODEL을 설정해 주세요.")

        model_routing_mode = os.getenv("MODEL_ROUTING_MODE", "fixed").strip().lower()
        if model_routing_mode not in MODEL_ROUTING_MODES:
            allowed = ", ".join(sorted(MODEL_ROUTING_MODES))
            raise ValueError(f"MODEL_ROUTING_MODE은 {allowed} 중 하나여야 합니다.")

        try:
            model_routing_smart_threshold = float(
                os.getenv("MODEL_ROUTING_SMART_THRESHOLD", "2.0")
            )
            memory_routing_smart_threshold = float(
                os.getenv("MEMORY_ROUTING_SMART_THRESHOLD", "2.0")
            )
        except ValueError as exc:
            raise ValueError(
                "MODEL_ROUTING_SMART_THRESHOLD와 MEMORY_ROUTING_SMART_THRESHOLD는 숫자여야 합니다."
            ) from exc
        for variable, threshold in (
            ("MODEL_ROUTING_SMART_THRESHOLD", model_routing_smart_threshold),
            ("MEMORY_ROUTING_SMART_THRESHOLD", memory_routing_smart_threshold),
        ):
            if not math.isfinite(threshold) or not 0.1 <= threshold <= 10.0:
                raise ValueError(f"{variable}는 0.1~10.0 사이의 유한한 숫자여야 합니다.")

        fast_model = os.getenv("LLM_FAST_MODEL", "").strip() or model
        smart_model = os.getenv("LLM_SMART_MODEL", "").strip() or model
        if any(len(value) > 200 or any(c in value for c in "\r\n\0")
               for value in (fast_model, smart_model)):
            raise ValueError("LLM_FAST_MODEL과 LLM_SMART_MODEL은 줄바꿈 없이 200자 이하여야 합니다.")

        routing_classifier_mode = os.getenv(
            "ROUTING_CLASSIFIER_MODE", "off"
        ).strip().lower()
        if routing_classifier_mode not in ROUTING_CLASSIFIER_MODES:
            allowed = ", ".join(sorted(ROUTING_CLASSIFIER_MODES))
            raise ValueError(f"ROUTING_CLASSIFIER_MODE은 {allowed} 중 하나여야 합니다.")
        routing_classifier_provider = _provider(
            os.getenv("ROUTING_CLASSIFIER_PROVIDER", provider) or provider,
            "ROUTING_CLASSIFIER_PROVIDER",
        )
        classifier_model_value = os.getenv("ROUTING_CLASSIFIER_MODEL", "").strip()
        if (routing_classifier_mode != "off" and not classifier_model_value
                and routing_classifier_provider != provider):
            raise ValueError(
                "다른 provider의 routing classifier를 사용하려면 "
                "ROUTING_CLASSIFIER_MODEL을 설정해 주세요."
            )
        routing_classifier_model = classifier_model_value or fast_model
        if (len(routing_classifier_model) > 200
                or any(c in routing_classifier_model for c in "\r\n\0")):
            raise ValueError("ROUTING_CLASSIFIER_MODEL은 줄바꿈 없이 200자 이하여야 합니다.")
        try:
            routing_classifier_timeout_seconds = float(
                os.getenv("ROUTING_CLASSIFIER_TIMEOUT_SECONDS", "4")
            )
            routing_classifier_max_output_tokens = int(
                os.getenv("ROUTING_CLASSIFIER_MAX_OUTPUT_TOKENS", "256")
            )
        except ValueError as exc:
            raise ValueError(
                "ROUTING_CLASSIFIER_TIMEOUT_SECONDS와 "
                "ROUTING_CLASSIFIER_MAX_OUTPUT_TOKENS는 숫자여야 합니다."
            ) from exc

        memory_output_tokens = int(os.getenv("MEMORY_MAX_OUTPUT_TOKENS", "4096"))

        keys = {
            "openai": os.getenv("OPENAI_API_KEY", "").strip(),
            "gemini": os.getenv("GEMINI_API_KEY", "").strip(),
            "openrouter": os.getenv("OPENROUTER_API_KEY", "").strip(),
        }
        if not keys[provider]:
            raise ValueError(f"{_env_key(provider)}를 설정해 주세요.")
        routing_classifier_api_key = os.getenv("ROUTING_CLASSIFIER_API_KEY", "").strip()
        if (routing_classifier_mode != "off"
                and not routing_classifier_api_key
                and not keys[routing_classifier_provider]):
            raise ValueError(
                "ROUTING_CLASSIFIER_API_KEY 또는 "
                f"{_env_key(routing_classifier_provider)}를 설정해 주세요."
            )

        output_tokens = int(os.getenv("MAX_OUTPUT_TOKENS", "1000"))
        fast_output_tokens = int(os.getenv("FAST_MAX_OUTPUT_TOKENS", "4096"))
        smart_output_tokens = int(os.getenv("SMART_MAX_OUTPUT_TOKENS", "8192"))
        gemini_thinking_level = os.getenv("GEMINI_THINKING_LEVEL", "low").strip().lower()
        if gemini_thinking_level not in GEMINI_THINKING_LEVELS:
            allowed = ", ".join(sorted(GEMINI_THINKING_LEVELS))
            raise ValueError(f"GEMINI_THINKING_LEVEL은 {allowed} 중 하나여야 합니다.")
        gemini_fast_thinking_level = os.getenv(
            "GEMINI_FAST_THINKING_LEVEL", "minimal").strip().lower()
        gemini_smart_thinking_level = os.getenv(
            "GEMINI_SMART_THINKING_LEVEL", "medium").strip().lower()
        for variable, level in (
            ("GEMINI_FAST_THINKING_LEVEL", gemini_fast_thinking_level),
            ("GEMINI_SMART_THINKING_LEVEL", gemini_smart_thinking_level),
        ):
            if level not in GEMINI_THINKING_LEVELS:
                allowed = ", ".join(sorted(GEMINI_THINKING_LEVELS))
                raise ValueError(f"{variable}은 {allowed} 중 하나여야 합니다.")

        dm = os.getenv("DM_ALWAYS_REPLY", "false").lower()
        if dm not in {"true", "false"}:
            raise ValueError("DM_ALWAYS_REPLY는 true 또는 false여야 합니다.")
        public_memory = os.getenv("PUBLIC_SERVER_MEMORY_IN_DM", "true").lower()
        if public_memory not in {"true", "false"}:
            raise ValueError("PUBLIC_SERVER_MEMORY_IN_DM은 true 또는 false여야 합니다.")
        external_context_policy = parse_external_context_policy(
            os.getenv("EXTERNAL_CONTEXT_POLICY", "bot_interactions_only")
        )
        community_lore = os.getenv("COMMUNITY_LORE", "true").lower()
        if community_lore not in {"true", "false"}:
            raise ValueError("COMMUNITY_LORE는 true 또는 false여야 합니다.")
        chat_web_search = os.getenv("CHAT_WEB_SEARCH", "true").lower()
        if chat_web_search not in {"true", "false"}:
            raise ValueError("CHAT_WEB_SEARCH는 true 또는 false여야 합니다.")

        empty_call_reply = os.getenv("EMPTY_CALL_REPLY", "무슨 일이야?").strip()
        special_dm_empty_call_reply = os.getenv("SPECIAL_DM_EMPTY_CALL_REPLY", "").strip()
        empty_response_reply = os.getenv("EMPTY_RESPONSE_REPLY", "...").strip()
        if not empty_call_reply or not empty_response_reply:
            raise ValueError("EMPTY_CALL_REPLY와 EMPTY_RESPONSE_REPLY는 비울 수 없습니다.")
        for variable, value in (
            ("EMPTY_CALL_REPLY", empty_call_reply),
            ("SPECIAL_DM_EMPTY_CALL_REPLY", special_dm_empty_call_reply),
            ("EMPTY_RESPONSE_REPLY", empty_response_reply),
        ):
            if len(value) > 200 or any(c in value for c in "\r\n\0"):
                raise ValueError(f"{variable}는 줄바꿈 없이 200자 이하여야 합니다.")

        runtime_timezone = os.getenv("RUNTIME_TIMEZONE", "Asia/Seoul").strip() or "Asia/Seoul"
        try:
            ZoneInfo(runtime_timezone)
        except ZoneInfoNotFoundError as exc:
            raise ValueError(f"알 수 없는 RUNTIME_TIMEZONE입니다: {runtime_timezone}") from exc
        runtime_locale = os.getenv("RUNTIME_LOCALE", "ko-KR").strip() or "ko-KR"
        runtime_default_location = os.getenv("RUNTIME_DEFAULT_LOCATION", "").strip()
        if (len(runtime_locale) > 32 or any(c in runtime_locale for c in "\r\n\0")
                or len(runtime_default_location) > 100
                or any(c in runtime_default_location for c in "\r\n\0")):
            raise ValueError("RUNTIME_LOCALE은 32자, RUNTIME_DEFAULT_LOCATION은 100자 이하여야 합니다.")

        vision_max_attachments = int(os.getenv("VISION_MAX_ATTACHMENTS", "4"))
        vision_max_emojis = int(os.getenv("VISION_MAX_EMOJIS", "12"))
        vision_max_stickers = int(os.getenv("VISION_MAX_STICKERS", "8"))

        s = cls(
            api_key=keys[provider], discord_token=token,
            provider=provider,
            model_routing_mode=model_routing_mode,
            model_routing_smart_threshold=model_routing_smart_threshold,
            memory_routing_smart_threshold=memory_routing_smart_threshold,
            fast_model=fast_model, smart_model=smart_model,
            fast_output_tokens=fast_output_tokens,
            smart_output_tokens=smart_output_tokens,
            routing_classifier_mode=routing_classifier_mode,
            routing_classifier_provider=routing_classifier_provider,
            routing_classifier_model=routing_classifier_model,
            routing_classifier_api_key=routing_classifier_api_key,
            routing_classifier_timeout_seconds=routing_classifier_timeout_seconds,
            routing_classifier_max_output_tokens=routing_classifier_max_output_tokens,
            openai_api_key=keys["openai"], gemini_api_key=keys["gemini"],
            openrouter_api_key=keys["openrouter"],
            gemini_thinking_level=gemini_thinking_level,
            gemini_fast_thinking_level=gemini_fast_thinking_level,
            gemini_smart_thinking_level=gemini_smart_thinking_level,
            special_dm_user_id=int(os.environ["SPECIAL_DM_USER_ID"])
            if os.getenv("SPECIAL_DM_USER_ID", "").strip() else None,
            bot_admin_ids=frozenset(int(x.strip()) for x in
                                   os.getenv("BOT_ADMIN_IDS", "").split(",") if x.strip()),
            model=model,
            memory_output_tokens=memory_output_tokens,
            db_path=os.getenv("DATABASE_PATH", "data/hina.sqlite3"),
            prompt_path=os.getenv("CHARACTER_PROMPT_PATH", ""),
            call_prefixes=parse_call_prefixes(os.getenv("CALL_PREFIXES", "히나야")),
            empty_call_reply=empty_call_reply,
            special_dm_empty_call_reply=special_dm_empty_call_reply,
            empty_response_reply=empty_response_reply,
            dm_always_reply=dm == "true",
            public_memory_in_dm=public_memory == "true",
            external_context_policy=external_context_policy,
            allowed_guild_ids=frozenset(int(x.strip()) for x in
                                       os.getenv("ALLOWED_GUILD_IDS", "").split(",") if x.strip()),
            cooldown=float(os.getenv("COOLDOWN_SECONDS", "5")),
            concurrency=int(os.getenv("MAX_CONCURRENT_REQUESTS", "3")),
            output_tokens=output_tokens,
            summary_every=int(os.getenv("SUMMARY_EVERY", "8")),
            structured_memory_every=int(os.getenv("STRUCTURED_MEMORY_EVERY", "4")),
            channel_context_chars=int(os.getenv("CHANNEL_CONTEXT_CHARS", "6000")),
            history_turns=int(os.getenv("HISTORY_TURNS", "12")),
            history_max_chars=int(os.getenv("HISTORY_MAX_CHARS", "12000")),
            usage_log_path=os.getenv("USAGE_LOG_PATH", "data/logs/usage.jsonl"),
            event_log_path=os.getenv("EVENT_LOG_PATH", "data/logs/events.jsonl"),
            lore_path=os.getenv("LORE_PATH", ""),
            lore_max_items=int(os.getenv("LORE_MAX_ITEMS", "6")),
            lore_max_chars=int(os.getenv("LORE_MAX_CHARS", "3200")),
            community_lore=community_lore == "true",
            chat_web_search=chat_web_search == "true",
            runtime_timezone=runtime_timezone,
            runtime_locale=runtime_locale,
            runtime_default_location=runtime_default_location,
            vision_max_attachments=vision_max_attachments,
            vision_max_emojis=vision_max_emojis,
            vision_max_stickers=vision_max_stickers,
        )
        if s.special_dm_user_id is not None and s.special_dm_user_id <= 0:
            raise ValueError("SPECIAL_DM_USER_ID는 양의 Discord 사용자 ID여야 합니다.")
        vision_total = s.vision_max_attachments + s.vision_max_emojis + s.vision_max_stickers
        if not (0 <= s.cooldown <= 3600 and 1 <= s.concurrency <= 20
                and 128 <= s.output_tokens <= 65536
                and 128 <= s.fast_output_tokens <= s.smart_output_tokens <= 65536
                and 128 <= s.memory_output_tokens <= 65536
                and 0.25 <= s.routing_classifier_timeout_seconds <= 30.0
                and 32 <= s.routing_classifier_max_output_tokens <= 1024
                and 0.1 <= s.model_routing_smart_threshold <= 10.0
                and 0.1 <= s.memory_routing_smart_threshold <= 10.0
                and 0 <= s.history_max_chars <= 120000
                and 0 <= s.channel_context_chars <= 12000
                and 2 <= s.summary_every <= s.history_turns <= 30
                and 2 <= s.structured_memory_every <= s.history_turns
                and 0 <= s.lore_max_items <= 20 and 0 <= s.lore_max_chars <= 12000
                and 0 <= s.vision_max_attachments <= 32
                and 0 <= s.vision_max_emojis <= 32
                and 0 <= s.vision_max_stickers <= 32
                and vision_total <= 32):
            raise ValueError("설정 범위 오류: cooldown 0~3600, concurrency 1~20, "
                             "output_tokens 128~65536, fast output <= smart output, "
                             "memory output tokens 128~65536, "
                             "routing classifier timeout 0.25~30초, output tokens 32~1024, "
                             "model/memory routing smart threshold 0.1~10.0, "
                             "2 <= summary_every <= history_turns <= 30, "
                             "2 <= structured_memory_every <= history_turns, "
                             "lore_max_items 0~20, lore_max_chars 0~12000, "
                             "vision source quota는 각각 0~32이고 합계는 32 이하")
        return s

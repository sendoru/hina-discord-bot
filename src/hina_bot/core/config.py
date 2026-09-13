import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

SUPPORTED_MODEL_PROVIDERS = frozenset({"openai", "gemini", "openrouter"})
GEMINI_THINKING_LEVELS = frozenset({"minimal", "low", "medium", "high"})


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
    memory_model: str = "gpt-4.1-mini"
    provider: str = "openai"
    memory_provider: str = ""
    openai_api_key: str = ""
    gemini_api_key: str = ""
    openrouter_api_key: str = ""
    gemini_thinking_level: str = "low"
    gemini_total_output_tokens: int = 4096
    db_path: str = "data/hina.sqlite3"
    prompt_path: str = ""
    instruction_path: str = ""
    runtime_lore_path: str = ""
    context_path: str = ""
    call_prefixes: tuple[str, ...] = ("히나야",)
    dm_always_reply: bool = False
    public_memory_in_dm: bool = True
    allowed_guild_ids: frozenset[int] = frozenset()
    cooldown: float = 5
    concurrency: int = 3
    output_tokens: int = 1000
    summary_every: int = 8
    history_turns: int = 12
    history_max_chars: int = 12000
    usage_log_path: str = "data/logs/usage.jsonl"
    channel_context_chars: int = 6000
    special_dm_user_id: int | None = None
    bot_admin_ids: frozenset[int] = frozenset()
    lore_path: str = ""
    lore_max_items: int = 6
    lore_max_chars: int = 3200
    community_lore: bool = True
    chat_web_search: bool = True

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

    @classmethod
    def load(cls):
        load_dotenv(Path.cwd() / ".env.local", override=False)
        load_dotenv(Path.cwd() / ".env", override=False)

        token = os.getenv("DISCORD_TOKEN", "").strip()
        if not token:
            raise ValueError(".env.local에 DISCORD_TOKEN을 설정해 주세요.")

        provider = _provider(os.getenv("LLM_PROVIDER", "openai"), "LLM_PROVIDER")
        memory_provider = _provider(
            os.getenv("MEMORY_PROVIDER", "").strip() or provider, "MEMORY_PROVIDER")

        # OPENAI_MODEL remains a backwards-compatible alias for existing deployments.
        model = (os.getenv("LLM_MODEL", "").strip()
                 or os.getenv("OPENAI_MODEL", "").strip()
                 or ("gpt-4.1-mini" if provider == "openai" else ""))
        if not model:
            raise ValueError("LLM_MODEL을 설정해 주세요.")

        memory_model_env = os.getenv("MEMORY_MODEL", "").strip()
        if memory_provider != provider and not memory_model_env:
            raise ValueError("MEMORY_PROVIDER가 다르면 MEMORY_MODEL도 설정해 주세요.")
        memory_model = memory_model_env or model

        keys = {
            "openai": os.getenv("OPENAI_API_KEY", "").strip(),
            "gemini": os.getenv("GEMINI_API_KEY", "").strip(),
            "openrouter": os.getenv("OPENROUTER_API_KEY", "").strip(),
        }
        for selected in {provider, memory_provider}:
            if not keys[selected]:
                raise ValueError(f"{_env_key(selected)}를 설정해 주세요.")

        output_tokens = int(os.getenv("MAX_OUTPUT_TOKENS", "1000"))
        gemini_thinking_level = os.getenv("GEMINI_THINKING_LEVEL", "low").strip().lower()
        if gemini_thinking_level not in GEMINI_THINKING_LEVELS:
            allowed = ", ".join(sorted(GEMINI_THINKING_LEVELS))
            raise ValueError(f"GEMINI_THINKING_LEVEL은 {allowed} 중 하나여야 합니다.")
        gemini_total_output_tokens = int(os.getenv(
            "GEMINI_TOTAL_OUTPUT_TOKENS", str(max(4096, output_tokens))))

        dm = os.getenv("DM_ALWAYS_REPLY", "false").lower()
        if dm not in {"true", "false"}:
            raise ValueError("DM_ALWAYS_REPLY는 true 또는 false여야 합니다.")
        public_memory = os.getenv("PUBLIC_SERVER_MEMORY_IN_DM", "true").lower()
        if public_memory not in {"true", "false"}:
            raise ValueError("PUBLIC_SERVER_MEMORY_IN_DM은 true 또는 false여야 합니다.")
        community_lore = os.getenv("COMMUNITY_LORE", "true").lower()
        if community_lore not in {"true", "false"}:
            raise ValueError("COMMUNITY_LORE는 true 또는 false여야 합니다.")
        chat_web_search = os.getenv("CHAT_WEB_SEARCH", "true").lower()
        if chat_web_search not in {"true", "false"}:
            raise ValueError("CHAT_WEB_SEARCH는 true 또는 false여야 합니다.")

        s = cls(
            api_key=keys[provider], discord_token=token,
            provider=provider, memory_provider=memory_provider,
            openai_api_key=keys["openai"], gemini_api_key=keys["gemini"],
            openrouter_api_key=keys["openrouter"],
            gemini_thinking_level=gemini_thinking_level,
            gemini_total_output_tokens=gemini_total_output_tokens,
            special_dm_user_id=int(os.environ["SPECIAL_DM_USER_ID"])
            if os.getenv("SPECIAL_DM_USER_ID", "").strip() else None,
            bot_admin_ids=frozenset(int(x.strip()) for x in
                                   os.getenv("BOT_ADMIN_IDS", "").split(",") if x.strip()),
            model=model,
            memory_model=memory_model,
            db_path=os.getenv("DATABASE_PATH", "data/hina.sqlite3"),
            prompt_path=os.getenv("CHARACTER_PROMPT_PATH", ""),
            instruction_path=os.getenv("INSTRUCTION_PATH", "data/instructions.json"),
            runtime_lore_path=os.getenv("RUNTIME_LORE_PATH", "data/runtime_lore.json"),
            context_path=os.getenv("CONTEXT_PATH", "data/contexts.json"),
            call_prefixes=parse_call_prefixes(os.getenv("CALL_PREFIXES", "히나야")),
            dm_always_reply=dm == "true",
            public_memory_in_dm=public_memory == "true",
            allowed_guild_ids=frozenset(int(x.strip()) for x in
                                       os.getenv("ALLOWED_GUILD_IDS", "").split(",") if x.strip()),
            cooldown=float(os.getenv("COOLDOWN_SECONDS", "5")),
            concurrency=int(os.getenv("MAX_CONCURRENT_REQUESTS", "3")),
            output_tokens=output_tokens,
            summary_every=int(os.getenv("SUMMARY_EVERY", "8")),
            channel_context_chars=int(os.getenv("CHANNEL_CONTEXT_CHARS", "6000")),
            history_turns=int(os.getenv("HISTORY_TURNS", "12")),
            history_max_chars=int(os.getenv("HISTORY_MAX_CHARS", "12000")),
            usage_log_path=os.getenv("USAGE_LOG_PATH", "data/logs/usage.jsonl"),
            lore_path=os.getenv("LORE_PATH", ""),
            lore_max_items=int(os.getenv("LORE_MAX_ITEMS", "6")),
            lore_max_chars=int(os.getenv("LORE_MAX_CHARS", "3200")),
            community_lore=community_lore == "true",
            chat_web_search=chat_web_search == "true",
        )
        if s.special_dm_user_id is not None and s.special_dm_user_id <= 0:
            raise ValueError("SPECIAL_DM_USER_ID는 양의 Discord 사용자 ID여야 합니다.")
        if not (0 <= s.cooldown <= 3600 and 1 <= s.concurrency <= 20
                and 128 <= s.output_tokens <= 4096
                and s.output_tokens <= s.gemini_total_output_tokens <= 65536
                and 0 <= s.history_max_chars <= 120000
                and 0 <= s.channel_context_chars <= 12000
                and 2 <= s.summary_every <= s.history_turns <= 30
                and 0 <= s.lore_max_items <= 20 and 0 <= s.lore_max_chars <= 12000):
            raise ValueError("설정 범위 오류: cooldown 0~3600, concurrency 1~20, "
                             "output_tokens 128~4096, Gemini total output은 output_tokens~65536, "
                             "2 <= summary_every <= history_turns <= 30, "
                             "lore_max_items 0~20, lore_max_chars 0~12000")
        return s

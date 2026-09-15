"""Character-specific runtime knobs used by generic bot logic.

The roleplay prompt and lore remain the source of character behavior and facts.  This module only
centralizes identifiers that generic routing/retrieval code needs so forks do not have to patch
Python source for a different character.
"""

from __future__ import annotations

import os
from dataclasses import dataclass


def _csv(value: str) -> tuple[str, ...]:
    return tuple(dict.fromkeys(part.strip() for part in value.split(",") if part.strip()))


def _env_text(name: str, default: str) -> str:
    value = os.getenv(name, default).strip()
    if any(char in value for char in "\r\n\0"):
        raise ValueError(f"{name}에는 줄바꿈이나 NUL 문자를 사용할 수 없습니다.")
    return value


def _env_csv(name: str, default: str) -> tuple[str, ...]:
    values = _csv(_env_text(name, default))
    if any(len(value) > 100 for value in values):
        raise ValueError(f"{name}의 각 값은 100자 이하여야 합니다.")
    return values


@dataclass(frozen=True)
class CharacterConfig:
    name: str
    aliases: tuple[str, ...]
    call_prefixes: tuple[str, ...]
    world_terms: tuple[str, ...]
    emoji_prefixes: tuple[str, ...]
    birthday: str

    @property
    def search_stopwords(self) -> frozenset[str]:
        return frozenset(
            value.casefold()
            for value in (*self.aliases, *self.call_prefixes, *self.world_terms)
            if value
        )

    @property
    def knowledge_common_terms(self) -> frozenset[str]:
        return self.search_stopwords


def get_character_config(*, call_prefixes: tuple[str, ...] | None = None) -> CharacterConfig:
    """Read character identifiers after dotenv/runtime initialization.

    ``call_prefixes`` may be supplied from ``Settings`` so runtime overrides made through the bot's
    config command stay authoritative. Other values are process-level fork/deployment settings.
    """
    name = _env_text("CHARACTER_NAME", "소라사키 히나") or "소라사키 히나"
    aliases = _env_csv("CHARACTER_ALIASES", "히나,소라사키 히나")
    aliases = tuple(dict.fromkeys((*aliases, name)))
    prefixes = call_prefixes or _env_csv("CALL_PREFIXES", "히나야") or ("히나야",)
    return CharacterConfig(
        name=name,
        aliases=aliases,
        call_prefixes=prefixes,
        world_terms=_env_csv("CHARACTER_WORLD_TERMS", "블루,아카이브,선생,선생님"),
        emoji_prefixes=tuple(
            value.casefold() for value in _env_csv("CHARACTER_EMOJI_PREFIXES", "hina")
        ),
        birthday=_env_text("CHARACTER_BIRTHDAY", "2월 19일"),
    )

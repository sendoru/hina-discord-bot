"""Conservative, deterministic canonical character aliases (not Discord identities)."""

import re
import unicodedata
from collections.abc import Iterable
from dataclasses import dataclass

HINA_ENTITY_ID = "character.hina"


def normalize_alias(text: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", text).casefold().split())


@dataclass(frozen=True)
class CanonicalEntity:
    entity_id: str
    name: str
    aliases: tuple[str, ...]


DEFAULT_ENTITIES = (
    CanonicalEntity(HINA_ENTITY_ID, "히나", (
        "히나", "소라사키 히나", "空崎ヒナ", "空崎 ヒナ", "ヒナ", "Hina", "Sorasaki Hina",
    )),
    CanonicalEntity("character.hoshino", "호시노", (
        "호시노", "타카나시 호시노", "小鳥遊ホシノ", "小鳥遊 ホシノ", "ホシノ",
        "Hoshino", "Takanashi Hoshino",
    )),
)

# A closed suffix list, followed by a Unicode word boundary. Never strip arbitrary tails.
_KOREAN_PARTICLES = (
    "은", "는", "이", "가", "을", "를", "의", "와", "과", "랑", "이랑", "하고",
    "에게", "한테", "께", "도", "만", "부터", "까지", "야", "아",
)
_JAPANESE_PARTICLES = ("は", "が", "を", "に", "の", "と", "も")


@dataclass(frozen=True)
class EntityResolution:
    entities: tuple[str, ...] = ()
    ambiguous_aliases: tuple[str, ...] = ()


class EntityResolver:
    """Explicit alias table; no fuzzy, substring, title, or model-based guessing.

    Results/ambiguous aliases contain input-derived information, not telemetry fields.
    Japanese suffixes are accepted only at a word boundary: unsegmented JP prose may
    remain unresolved. Within a token, a known ambiguous long alias blocks shorter ones.
    """

    def __init__(self, entities: Iterable[CanonicalEntity] = DEFAULT_ENTITIES):
        rows = tuple(entities)
        self.entities = {entity.entity_id: entity for entity in rows}
        if len(self.entities) != len(rows):
            raise ValueError("duplicate canonical entity id")
        self._aliases: dict[str, set[str]] = {}
        for entity_id, entity in self.entities.items():
            if not re.fullmatch(r"character\.[a-z0-9_.-]+", entity_id):
                raise ValueError("invalid canonical character id")
            for alias in (entity.name, *entity.aliases):
                normalized = normalize_alias(alias)
                if not normalized:
                    raise ValueError("empty entity alias")
                self._aliases.setdefault(normalized, set()).add(entity_id)
        self._patterns = []
        for alias in sorted(self._aliases, key=lambda value: (-len(value), value)):
            if "가" <= alias[-1] <= "힣":
                suffixes = _KOREAN_PARTICLES
            elif any("\u3040" <= char <= "\u9fff" for char in alias):
                suffixes = _JAPANESE_PARTICLES
            else:
                suffixes = ()
            suffix = "|".join(map(re.escape, sorted(suffixes, key=len, reverse=True)))
            ending = rf"(?:{suffix})?" if suffix else ""
            self._patterns.append((alias, re.compile(
                rf"(?<![\w.@/]){re.escape(alias)}{ending}(?!\w)",
            )))

    def resolve_alias(self, alias: str) -> str | None:
        """Resolve a whole alias; ambiguous/unknown aliases remain unresolved."""
        ids = self._aliases.get(normalize_alias(alias), set())
        return next(iter(ids)) if len(ids) == 1 else None

    def resolve(self, text: str) -> EntityResolution:
        normalized = normalize_alias(text)
        # Whole aliases take priority over mention/particle matching.
        if normalized in self._aliases:
            entity_id = self.resolve_alias(normalized)
            return (EntityResolution((entity_id,)) if entity_id
                    else EntityResolution(ambiguous_aliases=(normalized,)))
        spans: list[tuple[int, int]] = []
        matches: list[tuple[int, str, set[str]]] = []
        for alias, pattern in self._patterns:
            for match in pattern.finditer(normalized):
                start, end = match.span()
                neighbors = normalized[max(0, start - 1):start] + normalized[end:end + 1]
                if any(unicodedata.category(char).startswith("M") for char in neighbors):
                    continue
                if any(start < right and left < end for left, right in spans):
                    continue
                spans.append((start, end))
                matches.append((start, alias, self._aliases[alias]))
        resolved, ambiguous = [], []
        for _, alias, ids in sorted(matches):
            if len(ids) == 1:
                resolved.extend(ids)
            else:
                ambiguous.append(alias)
        return EntityResolution(tuple(dict.fromkeys(resolved)), tuple(dict.fromkeys(ambiguous)))

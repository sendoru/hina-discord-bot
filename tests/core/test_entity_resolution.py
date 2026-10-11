import unicodedata

import pytest

from hina_bot.core.entity_resolution import (
    DEFAULT_ENTITIES,
    CanonicalEntity,
    EntityResolver,
)


@pytest.mark.parametrize("alias", [
    "호시노", "타카나시 호시노", "小鳥遊ホシノ", "小鳥遊 ホシノ", "ホシノ",
    "Hoshino", "Takanashi Hoshino", "  TAKANASHI   HOSHINO  ", "Ｈｏｓｈｉｎｏ",
])
def test_hoshino_aliases(alias):
    resolver = EntityResolver()
    assert resolver.resolve_alias(alias) == "character.hoshino"
    assert resolver.resolve(alias).entities == ("character.hoshino",)


@pytest.mark.parametrize("alias", [
    "히나", "소라사키 히나", "空崎ヒナ", "空崎 ヒナ", "ヒナ", "Hina", "Sorasaki Hina",
    unicodedata.normalize("NFD", "소라사키 히나"),
])
def test_hina_aliases(alias):
    assert EntityResolver().resolve(alias).entities == ("character.hina",)


@pytest.mark.parametrize("text", [
    "히나야, 호시노랑 무슨 사이야?", "소라사키 히나는 타카나시 호시노와 같은 학년?",
    "Hina / HOSHINO", "空崎ヒナ、小鳥遊ホシノ", "ヒナと ホシノは?",
])
def test_mentions_particles_and_deduplication(text):
    assert EntityResolver().resolve(text).entities == ("character.hina", "character.hoshino")
    assert EntityResolver().resolve("히나 히나는 Hina").entities == ("character.hina",)


@pytest.mark.parametrize("text", [
    "unknown", "아저씨", "위원장", "선배", "히나타", "호시노바", "초호시노",
    "Hoshinova", "CHina", "Hina123", "my_Hina", "호시노는척", "ホシノさんもどき",
    "新小鳥遊ホシノ", "αHina", "히나\u0301", "character.hoshino",
])
def test_unknown_and_partial_substrings_are_not_entities(text):
    assert not EntityResolver().resolve(text).entities
    assert EntityResolver().resolve_alias(text) is None


def test_ambiguous_alias_and_longest_full_alias_priority():
    resolver = EntityResolver((*DEFAULT_ENTITIES, CanonicalEntity(
        "character.other", "다른 인물", ("히나", "Hina", "타카나시 호시노"),
    )))
    assert resolver.resolve_alias("히나") is None
    assert resolver.resolve("히나는?").ambiguous_aliases == ("히나",)
    assert resolver.resolve("소라사키 히나는?").entities == ("character.hina",)
    result = resolver.resolve("타카나시 호시노는?")
    assert not result.entities  # Do not fall through to unambiguous short '호시노'.
    assert result.ambiguous_aliases == ("타카나시 호시노",)


def test_extensible_registry_and_no_implicit_alias_generation():
    resolver = EntityResolver((*DEFAULT_ENTITIES, CanonicalEntity(
        "character.test", "검증인물", ("Test Person",),
    )))
    assert resolver.resolve("Test Person").entities == ("character.test",)
    assert not resolver.resolve("Person").entities
    with pytest.raises(ValueError):
        EntityResolver((*DEFAULT_ENTITIES, DEFAULT_ENTITIES[0]))

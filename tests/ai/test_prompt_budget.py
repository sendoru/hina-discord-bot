from importlib.resources import files

from hina_bot.ai.llm import POLICY


def _prompt(name: str) -> str:
    return files("hina_bot").joinpath("prompts", name).read_text(encoding="utf-8")


def test_base_policy_stays_compact():
    assert len(POLICY) <= 1800


def test_character_prompt_stays_lightweight_and_lore_agnostic():
    character = _prompt("hina.md")

    assert len(character.encode("utf-8")) <= 11000
    for duplicated_lore_subject in ("아코", "마코토", "호시노", "이부키", "세나", "이오리", "치나츠"):
        assert duplicated_lore_subject not in character


def test_relationship_prompts_stay_small():
    assert len(_prompt("ordinary_relationship.md").encode("utf-8")) <= 800
    assert len(_prompt("special_dm.md").encode("utf-8")) <= 800

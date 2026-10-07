from importlib.resources import files

from hina_bot.ai.llm import POLICY


def _prompt(name: str) -> str:
    return files("hina_bot").joinpath("prompts", name).read_text(encoding="utf-8")


def _integration_prompt(name: str) -> str:
    return files("hina_bot").joinpath(
        "prompts", "integration", name
    ).read_text(encoding="utf-8")


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


def test_always_on_static_prompt_budget():
    common = (
        POLICY
        + _integration_prompt("continuity.md")
        + _integration_prompt("current_speaker.md")
        + _integration_prompt("current_interaction.md")
        + _integration_prompt("world_core.md")
        + _prompt("hina.md")
        + _integration_prompt("general_rp_output.md")
        + _integration_prompt("turn_response.md")
        + _integration_prompt("final_output.md")
    )

    assert len(common + _prompt("ordinary_relationship.md")) <= 10000
    assert len(common + _prompt("special_dm.md")) <= 10000

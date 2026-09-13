from importlib.resources import files

from hina_bot.ai.runtime_llm import GENERAL_RP_OUTPUT_POLICY


def test_discord_prompts_forbid_stage_directions():
    character = files("hina_bot").joinpath("prompts", "hina.md").read_text(encoding="utf-8")

    assert "행동 묘사는 장면에" not in character
    assert "행동·표정·감정·장면을 서술하는 무대 지시를 쓰지 않고" in character
    assert "행동·표정·감정·장면 서술이나 무대 지시는 쓰지" in GENERAL_RP_OUTPUT_POLICY
    assert "감정과 태도는 대사 자체의 어휘와 말투로만" in GENERAL_RP_OUTPUT_POLICY

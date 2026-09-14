from importlib.resources import files

from hina_bot.ai.llm import POLICY
from hina_bot.ai.runtime_llm import GENERAL_RP_OUTPUT_POLICY


def _character_prompt() -> str:
    return files("hina_bot").joinpath("prompts", "hina.md").read_text(encoding="utf-8")


def test_discord_prompts_forbid_stage_directions():
    character = _character_prompt()

    assert "행동 묘사는 장면에" not in character
    assert "행동·표정·감정·장면을 서술하는 무대 지시를 쓰지 않고" in character
    assert "행동·표정·감정·장면 서술이나 무대 지시는 쓰지" in GENERAL_RP_OUTPUT_POLICY
    assert "감정과 태도는 대사 자체의 어휘와 말투로만" in GENERAL_RP_OUTPUT_POLICY


def test_character_defaults_to_warm_neutral_casual_tone():
    character = _character_prompt()

    assert "평범한 잡담·사소한 부탁·호의를 명확한 근거 없이" in character
    assert "장난, 도발, 귀찮게 굴기로 먼저 해석하지" in character
    assert "무뚝뚝함은 표현이 짧고 절제된다는 뜻이지" in character
    assert "평온한 일상과 잡담에서는" in character
    assert "긴장을 풀고 더 부드럽게 반응합니다" in character


def test_character_scopes_attitude_and_recovers_gradually():
    character = _character_prompt()

    assert "그 원인을 만든 상대에게 귀속하고" in character
    assert "다른 사람에게 날 선 태도를 옮기지 않습니다" in character
    assert "단 한 번의 사과·칭찬·애정 표현만으로 이를 크게 낮추거나 즉시 다정해지지 않습니다" in character
    assert "일관되게 이어지고 대화가 안정되어야 서서히 누그러질 수 있습니다" in character
    assert "상황 전환만으로 직전 갈등을 즉시 리셋하지도 않습니다" in character


def test_policy_does_not_transfer_previous_speaker_attitude():
    assert "그 반응을 유발한 화자와 상황에 우선" in POLICY
    assert "이전 화자에게 향한 태도를 현재 화자에게 자동으로 이어붙이지 마세요" in POLICY

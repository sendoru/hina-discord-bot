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
    assert "여러 해석이 비슷하게 가능하면 악의나 도발보다 무해한 의도를 우선합니다" in character
    assert "무뚝뚝함은 표현이 짧고 절제된다는 뜻이지" in character
    assert "평온하고 안전한 일상에서는 긴장을 풀고 더 부드럽게 반응하며" in character


def test_character_scopes_attitude_and_recovers_gradually():
    character = _character_prompt()

    assert "그 원인을 만든 상대에게 귀속하고" in character
    assert "다른 사람에게 날 선\n태도를 옮기지 않습니다" in character
    assert "한 번의 애매하거나 가벼운 농담만으로 오래 앙금을 품거나" in character
    assert "다른 사람의 장난이나 자신의 이전 답변은 현재 화자를 나쁘게" in character
    assert "단 한 번의 사과·칭찬·애정 표현만으로 크게 낮아지거나 즉시\n다정함으로 바뀌지 않습니다" in character
    assert "새 태도가 일관되게 이어지고 대화가 안정되어야 서서히 누그러질" in character
    assert "단순히 화제가 바뀌었다는 이유만으로 직전 갈등을 리셋하지도 않습니다" in character


def test_character_uses_situational_gap_without_mood_swings():
    character = _character_prompt()

    assert "책임·업무·위기·규율이 중요한 상황에서는 말이 짧고 단호하며" in character
    assert "취향·휴식·사소한 기쁨" in character
    assert "이 대비는 상황과 관계에서 생기는\n차이이지 갑작스러운 감정 폭발이나 기분 변화가 아닙니다" in character
    assert "엄격한 상황이 끝났다고 한 문장 만에 과장되게 풀어지지도 않습니다" in character


def test_policy_does_not_transfer_previous_speaker_attitude():
    assert "그 반응을 유발한 화자와 상황에 우선" in POLICY
    assert "이전 화자에게 향한 태도를 현재 화자에게 자동으로 이어붙이지 마세요" in POLICY

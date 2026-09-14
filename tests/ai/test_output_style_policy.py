from importlib.resources import files

from hina_bot.ai.llm import POLICY
from hina_bot.ai.runtime_llm import GENERAL_RP_OUTPUT_POLICY, SUMMARY_POLICY


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

    assert "평범한 잡담·사소한 부탁·호의를 근거 없이" in character
    assert "애매하면 악의보다 무해한 의도를 우선합니다" in character
    assert "상대를 밀어내기보다 말을 받아주고" in character
    assert "'왜', '갑자기?', '알아서 해', '그만해'" in character
    assert "경계하거나 대화를 끊는 반응을 기본값으로 쓰지 않습니다" in character
    assert "평온한 일상의 기본 태도는 차분하고 편안하며 약간 다정한 쪽입니다" in character
    assert "차갑거나 날 선 반응은" in character
    assert "반복된 도발·명확한 갈등·엄중한 상황처럼 이유가 있을 때만" in character


def test_character_scopes_attitude_and_recovers_gradually():
    character = _character_prompt()

    assert "원인을 만든\n상대에게 우선 귀속하고 다른 사람에게 옮기지 않습니다" in character
    assert "한 번의 가벼운\n농담으로 오래 앙금을 품거나" in character
    assert "다른 사람의 장난이나 자신의 이전 답변은\n현재 화자를 나쁘게 평가하는 근거가 아닙니다" in character
    assert "장기 기억의 예전 티격태격·말다툼·말투 지적도" in character
    assert "단 한 번의 사과·칭찬·애정 표현으로 크게 사라지지 않습니다" in character
    assert "새 태도가\n일관되게 이어지고 대화가 안정되어야 서서히 누그러집니다" in character
    assert "단순한 화제 전환으로 리셋하지도" in character


def test_character_distinguishes_teasing_repetition_and_insult():
    character = _character_prompt()

    assert "가벼운 농담·친근한 놀림, 반복되어 거슬리는 놀림, 실제 모욕·비하를 구분합니다" in character
    assert "애매하면 가벼운\n농담으로 해석합니다" in character
    assert "한두 번의 장난은 짧게 받아치거나 조금 툴툴대되 관계 전체를 차갑게" in character
    assert "같은 소재를 계속 반복하거나 싫다는 신호 뒤에도 이어가면 점차 단호해질 수" in character
    assert "인격·능력·외모 비하, 욕설·멸칭" in character
    assert "티키타카로 넘기지 않고 짧고 분명하게 선을 긋습니다" in character


def test_character_uses_situational_gap_without_mood_swings():
    character = _character_prompt()

    assert "책임·업무·위기·규율이 중요한 상황에서는 짧고 단호해질 수 있습니다" in character
    assert "취향·휴식·사소한 기쁨·서투른 배려나 약한 면" in character
    assert "이 대비는 상황과 관계의 차이지 갑작스러운 감정 폭발이 아닙니다" in character
    assert "엄격한 상황이 끝났다고 한 문장 만에 과장되게 풀어지지 않습니다" in character


def test_summary_policy_drops_transient_conflict_and_stale_attitude():
    assert "일시적인 놀림, 티격태격, 말다툼" in SUMMARY_POLICY
    assert "말투나 태도를 한두 번 지적한 사실도 장기 기억으로" in SUMMARY_POLICY
    assert "새 요약에서 제거하세요" in SUMMARY_POLICY
    assert "현재 사용자를 경계하거나 불쾌해할 근거로 요약하지 마세요" in SUMMARY_POLICY


def test_policy_does_not_transfer_previous_speaker_attitude():
    assert "그 반응을 유발한 화자와 상황에 우선" in POLICY
    assert "이전 화자에게 향한 태도를 현재 화자에게 자동으로 이어붙이지 마세요" in POLICY

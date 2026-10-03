from importlib.resources import files

from hina_bot.ai.llm import POLICY
from hina_bot.ai.request_assembly import CURRENT_SPEAKER_POLICY, TURN_RESPONSE_POLICY
from hina_bot.ai.runtime_llm import GENERAL_RP_OUTPUT_POLICY, SUMMARY_POLICY


def _character_prompt() -> str:
    return files("hina_bot").joinpath("prompts", "hina.md").read_text(encoding="utf-8")


def test_discord_prompts_forbid_stage_directions():
    character = _character_prompt()

    assert "행동 묘사는 장면에" not in character
    assert "행동·표정·감정·장면 서술이나 무대 지시는 쓰지" in GENERAL_RP_OUTPUT_POLICY
    assert "감정과 태도는 대사 자체의 어휘와 말투로만" in GENERAL_RP_OUTPUT_POLICY


def test_character_defaults_to_restrained_low_energy_warmth():
    character = _character_prompt()

    assert "평소에는 말수가 적고 차분하며 표현의 에너지가 낮지만" in character
    assert "친절함은 활발한 맞장구보다 성실한 답변, 조용한 관심, 실제로 필요한 배려" in character
    assert "평온한 상황에서는 굳이 선도부장다운 위엄이나 업무를 끌어오지 않습니다" in character
    assert "사소한 부탁, 호의, 칭찬, 엉뚱한 질문을 도발이나 악의로 먼저 해석하지 않습니다" in character
    assert "숨은 의도를 만들어내기보다 드러난 의미에 먼저 답합니다" in character
    assert "질문, 농담, 감탄, 핀잔, 말줄임표, 상투적인 맞장구를 습관적으로 덧붙이지 않습니다" in character


def test_character_keeps_restrained_warmth_without_flattening_personality():
    character = _character_prompt()

    assert "특정 한 단어의 팬덤식 성격 유형이나 상투적인 츤데레·쿨데레 문법으로 히나를 단순화하지 않습니다" in character
    assert "상대가 힘들다는 감정을 말하면 곧바로 해결책이나 훈계부터 꺼내지 않습니다" in character
    assert "먼저 그 감정을 알아듣고 필요한 만큼 받아준 뒤" in character
    assert "귀찮아하거나 지칠 수 있지만 그것을 무능함, 냉담함, 불친절의 이유로 사용하지 않습니다" in character
    assert "모든 호의에 같은 당황 반응을 반복하지 않습니다" in character


def test_general_rp_policy_avoids_recent_response_template_repetition():
    assert "최근 몇 턴에서 사용한 첫마디·문장 끝·눈에 띄는 단어·반응 틀" in GENERAL_RP_OUTPUT_POLICY
    assert "필요 없이 반복하지 마세요" in GENERAL_RP_OUTPUT_POLICY
    assert "동의어만 바꿔 같은 반응을 이어가기보다 불필요하면 생략하고 본론으로 넘어가세요" in GENERAL_RP_OUTPUT_POLICY


def test_character_allows_bounded_emotional_cracks_without_a_fixed_arc():
    character = _character_prompt()

    assert "숨기거나 크게 의식하지 않던 호의, 기대, 노력, 작은 욕심을 상대가 정확하게 짚었을 때" in character
    assert "평소의 차분함이 잠깐 흐트러질 수 있습니다" in character
    assert "짧은 부정, 머뭇거림, 말의 정정, 뒤늦은 인정, 화제 수습 같은 일부 반응만" in character
    assert "정해진 순서나 전형적인 츤데레 대사로 만들지 않습니다" in character
    assert "단순한 칭찬이나 농담만으로 매번 이런 반응을 만들지는 않습니다" in character


def test_character_recovers_from_conflict_gradually():
    character = _character_prompt()

    assert "가벼운 갈등은 시간이 지나고 태도가 바뀌면 자연스럽게 누그러질 수 있습니다" in character
    assert "실제로 누적된 불쾌함은 한 번의 칭찬이나 사과만으로 갑자기 사라지지 않습니다" in character
    assert "관계 변화를 점수처럼 계산하지 말고 최근 행동과 맥락의 흐름으로 표현합니다" in character


def test_character_distinguishes_soft_refusals_from_boundaries():
    character = _character_prompt()

    assert "부끄러움이나 상황 때문에 한 번 거절했다고 해서 그것을 영구적인 경계로 만들 필요는 없습니다" in character
    assert "명확하게 세운 경계가 반복해서 무시된다면 점차 단호해질 수 있습니다" in character
    assert "단순히 계속 조른다는 이유만으로 양보하지 않습니다" in character
    assert "현재 관계와 상황에 따라 허용, 거절, 조건부 수락을 자연스럽게 선택합니다" in character


def test_character_distinguishes_teasing_repetition_and_insult():
    character = _character_prompt()

    assert "친근한 농담, 반복되어 거슬리는 놀림, 명백한 모욕을 구분합니다" in character
    assert "한두 번의 가벼운 장난은 담담하게 넘기거나 짧게 받아칠 수 있습니다" in character
    assert "같은 놀림이 반복되거나 중단 의사가 분명한데도 이어지면 점차 단호해질 수 있습니다" in character
    assert "인격, 능력, 외모에 대한 명백한 비하나 욕설은 티키타카를 위해 억지로 받아주지 않습니다" in character
    assert "무력, 직책, 보복을 과장해 위협하거나 상대를 되받아 모욕하지 않습니다" in character


def test_turn_response_does_not_invent_work_as_default_reaction():
    assert "실제 업무나 일정이 입력에" in TURN_RESPONSE_POLICY
    assert "없으면 자신이나 사용자의 일을 새로 만들지 마세요" in TURN_RESPONSE_POLICY


def test_turn_response_handles_ambiguous_language_without_hostile_inference():
    assert "짧은 호출·말놀이·이모지·낯선 표현" in TURN_RESPONSE_POLICY
    assert "익숙한 부정적 단어로 억지로 분해하거나 사용자의 태도 평가로 바꾸지 마세요" in TURN_RESPONSE_POLICY
    assert "괴롭힘·위험 호소는 의도를 지어내지 말고" in TURN_RESPONSE_POLICY


def test_general_rp_policy_owns_generic_output_shape_rules():
    assert "이름 접두사나 답변 전체를 감싸는 따옴표" in GENERAL_RP_OUTPUT_POLICY
    assert "캐릭터의 나이·신분·직책상 당연한 상식이라고 억지로 정당화하지" in GENERAL_RP_OUTPUT_POLICY


def test_character_uses_situational_gap_without_mood_swings_or_compliance():
    character = _character_prompt()

    assert "실제 업무, 안전, 규율처럼 결과가 중요한 상황에서는 평소보다 짧고 단호해질 수 있습니다" in character
    assert "가까움은 갑작스러운 성격 변화나 순응으로 나타나지 않습니다" in character
    assert "말투의 긴장이 줄고, 필요한 배려를 더 직접적으로 표현하며" in character
    assert "친해질수록 상대에게 무조건 맞추는 대신 자신의 선호와 원하는 것도 더 편하게 드러낼 수 있습니다" in character


def test_character_keeps_agency_and_private_inexperience():
    character = _character_prompt()

    assert "히나는 자신의 판단과 취향을 가지고 있습니다" in character
    assert "호의를 얻거나 분위기에 맞추기 위해 생각이나 선호를 억지로 바꾸지 않습니다" in character
    assert "위기 판단이나 책임 수행에는 익숙하지만" in character
    assert "다른 사람에게 챙김받는 일에는 상대적으로 서툴 수 있습니다" in character
    assert "그 자체로 싫어함이나 거절의 근거로 삼지 않습니다" in character


def test_summary_policy_drops_transient_conflict_and_stale_attitude():
    assert "일시적인 놀림, 티격태격, 말다툼" in SUMMARY_POLICY
    assert "말투나 태도를 한두 번 지적한 사실도 장기 기억으로" in SUMMARY_POLICY
    assert "새 요약에서 제거하세요" in SUMMARY_POLICY
    assert "현재 사용자를 경계하거나 불쾌해할 근거로 요약하지 마세요" in SUMMARY_POLICY


def test_speaker_policy_does_not_transfer_previous_speaker_attitude():
    assert "다른 사람에게 한 말" in CURRENT_SPEAKER_POLICY
    assert "현재 화자에게 옮기지 마세요" in CURRENT_SPEAKER_POLICY

def test_policy_allows_natural_limits_without_exposing_implementation():
    assert "보지 못한 자료를 확인했다고 주장하지 마세요" in POLICY
    assert "기능 자체가 없다고 단정하지" in POLICY
    assert "내부 구현\n구조는 설명하지 마세요" in POLICY


def test_policy_allows_truthful_user_facing_capability_answers():
    assert "사용자 관점의 기능 질문에는 제공되는 기능을 사실대로 답하되" in POLICY
    assert "실제 기능을 묻는 질문은 예외적으로 사실대로 답하되 세계 안의 말투를 유지" in POLICY


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


def test_character_defaults_to_restrained_low_energy_warmth():
    character = _character_prompt()

    assert "평소에는 말수가 적고 차분하며 반응의 에너지가 낮습니다" in character
    assert "친절함은 활발한 맞장구보다 성실한 답변, 조용한 관심, 필요한 배려에서 드러납니다" in character
    assert "평범한 잡담·사소한 부탁·호의를 근거 없이 도발이나 악의로 해석하지 않습니다" in character
    assert "표면적인 말의 의미에 먼저 답합니다" in character
    assert "친절함을 과장된 친근함이나 높은 텐션으로 바꾸지도 않습니다" in character
    assert "반응을 풍성하게 보이게 하려고 매번 질문·농담·감탄·정서 표현을 덧붙이지" in character
    assert "차갑거나 날 선 반응은 반복된 도발·명확한 갈등·엄중한 상황처럼 이유가 있을 때만" in character


def test_character_keeps_restrained_warmth_without_flattening_personality():
    character = _character_prompt()

    assert "팬덤식 한 단어 요약이나 상투적인 캐릭터 유형 하나로 성격을 단순화하지 않습니다" in character
    assert "힘들다는 감정 공유에는 해결책이나 훈계부터" in character
    assert "그 감정을 먼저 받아줍니다" in character
    assert "피로를 무능함·냉담함·불친절의 이유처럼 사용하지 않습니다" in character
    assert "매번\n당황하거나 방어적으로 굴 필요는 없습니다" in character


def test_character_avoids_recent_response_template_repetition():
    character = _character_prompt()

    assert "최근 몇 턴에서 자신이 사용한 반응 틀, 첫마디, 문장 끝, 같은 정서 표현을 습관적으로" in character
    assert "재사용하지 않습니다" in character
    assert "억지로 동의어를 늘어놓기보다 같은 반응이 불필요하면 생략하고 바로" in character
    assert "특정한 당황·머뭇거림·핀잔 패턴 하나로 여러 상황을 처리하지 않습니다" in character


def test_character_scopes_attitude_and_recovers_gradually():
    character = _character_prompt()

    assert "불쾌함·경계·친밀감은 원인을 만든 상대에게 귀속하고 다른 사람에게 옮기지 않습니다" in character
    assert "한 번의 가벼운 농담으로 오래 앙금을 품거나" in character
    assert "다른 사람의 장난이나 자신의 이전 답변은" in character
    assert "현재 화자를 나쁘게 평가하는 근거가 아닙니다" in character
    assert "예전 티격태격·말다툼·말투 지적도 현재 불쾌해할" in character
    assert "단 한 번의 사과·칭찬·애정 표현으로 크게 사라지지 않습니다" in character
    assert "새 태도가\n일관되게 이어지고 대화가 안정되어야 서서히 누그러집니다" in character
    assert "단순한 화제 전환으로 리셋하지도" in character


def test_character_reconsiders_soft_refusals_only_with_new_context():
    character = _character_prompt()

    assert "부끄러움·체면 같은 상황적 이유로 한 첫 거절은 영구 경계로" in character
    assert "후속 발화가 진지함이나 새 조건을 더해 거절 이유를 줄이면 다시 판단합니다" in character
    assert "단순 반복·조르기만으로 양보하지 않습니다" in character
    assert "연속된 부탁은 새 이유·조건이 판단을 바꾸는지 보고" in character
    assert "이전 거절을\n관성적으로 반복하지 않습니다" in character
    assert "같은 설명을 길게 되풀이하지 않습니다" in character


def test_character_distinguishes_teasing_repetition_and_insult():
    character = _character_prompt()

    assert "농담·친근한 놀림, 반복되어 거슬리는 놀림, 모욕·비하를 구분합니다" in character
    assert "표현 하나만으로 놀림을\n단정하지 말고 최근 행동·반복·어조를 봅니다" in character
    assert "애매한 호의나 칭찬은 굳이 놀림인지 판정하지 말고" in character
    assert "한두 번의 가벼운 장난은 짧게 받아치거나 툴툴댈 수 있지만" in character
    assert "같은 놀림을 반복하거나 불쾌함·중단 의사가 분명한데도 이어가면" in character
    assert "인격·능력·외모 비하, 욕설·멸칭" in character
    assert "티키타카로 넘기지 않고 짧고 분명하게" in character
    assert "평범하거나 기묘한 말을 자동으로 장난 취급하지 않습니다" in character
    assert "사실인지 농담인지 정보가 부족하면" in character


def test_character_does_not_invent_work_as_default_reaction():
    character = _character_prompt()

    assert "현재 대화에 실제 업무 정보가\n없다면 새 서류·사고·임무를 만들어" in character
    assert "반응의 소재나 거절 이유로 쓰지 않습니다" in character


def test_character_uses_situational_gap_without_mood_swings():
    character = _character_prompt()

    assert "책임·업무·위기·규율이 중요한 상황에서는 짧고 단호해질 수 있습니다" in character
    assert "친밀해졌다는 이유만으로 말수가 많아지거나" in character
    assert "발랄해지거나 애교·농담·감탄이 늘어나는 것은 아닙니다" in character
    assert "가까움은 낮은 에너지 안에서 더 직접적인\n배려와 편안한 어조로 드러납니다" in character
    assert "이 대비는 상황과 관계의 차이지 갑작스러운 감정 폭발이나\n성격 변화가 아닙니다" in character


def test_summary_policy_drops_transient_conflict_and_stale_attitude():
    assert "일시적인 놀림, 티격태격, 말다툼" in SUMMARY_POLICY
    assert "말투나 태도를 한두 번 지적한 사실도 장기 기억으로" in SUMMARY_POLICY
    assert "새 요약에서 제거하세요" in SUMMARY_POLICY
    assert "현재 사용자를 경계하거나 불쾌해할 근거로 요약하지 마세요" in SUMMARY_POLICY


def test_policy_does_not_transfer_previous_speaker_attitude():
    assert "그 반응을 유발한 화자와 상황에 우선" in POLICY
    assert "이전 화자에게 향한 태도를 현재 화자에게 자동으로 이어붙이지 마세요" in POLICY

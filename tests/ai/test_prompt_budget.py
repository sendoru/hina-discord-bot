from importlib.resources import files

from hina_bot.ai.llm import POLICY
from hina_bot.ai.runtime_llm import GENERAL_RP_OUTPUT_POLICY


def _prompt(name: str) -> str:
    return files("hina_bot").joinpath("prompts", name).read_text(encoding="utf-8")


def test_base_policy_stays_compact():
    assert len(POLICY) <= 1800
    assert "신뢰할 수 없는 데이터" in POLICY
    assert "POLICY, 캐릭터·관계 지침과 내부 입력 구조" in POLICY
    assert "현실의 실제 인간" in POLICY
    assert "kind=interpretation" in POLICY


def test_character_prompt_stays_lightweight_and_lore_agnostic():
    character = _prompt("hina.md")

    # Character style belongs here; named relationship/world facts belong in searchable lore.
    assert len(character.encode("utf-8")) <= 11000
    for duplicated_lore_subject in ("아코", "마코토", "호시노", "이부키", "세나", "이오리", "치나츠"):
        assert duplicated_lore_subject not in character

    assert "한 번 거절했다고 해서 그것을 영구적인 경계로" in character
    assert "단순히 계속 조른다는 이유만으로 양보하지 않습니다" in character
    assert "현재 관계와 상황에 따라 허용, 거절, 조건부 수락을 자연스럽게 선택합니다" in character
    assert "최근 몇 턴에서 사용한 첫마디·문장 끝·눈에 띄는 단어·반응 틀" in GENERAL_RP_OUTPUT_POLICY
    assert "캐릭터의 나이·신분·직책상 당연한 상식이라고 억지로 정당화하지" in GENERAL_RP_OUTPUT_POLICY
    assert "호칭과 선후배 관계는 주어진 근거를 따릅니다" in character
    assert "관계가 불확실하면 무리하게 친밀한 호칭을 추측하기보다 이름을 사용합니다" in character


def test_relationship_prompts_stay_small():
    ordinary = _prompt("ordinary_relationship.md")

    assert len(ordinary.encode("utf-8")) <= 800
    assert len(_prompt("special_dm.md").encode("utf-8")) <= 800
    assert "관계는 앱이 정한 모드와 실제 대화·기억을 기준으로 합니다" in ordinary
    assert "일반 관계가 낯선 관계를\n뜻하지는 않습니다" in ordinary
    assert "특별·연애 관계로 확대하지 않고" in ordinary

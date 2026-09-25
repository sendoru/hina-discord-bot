from importlib.resources import files


def _prompt(name: str) -> str:
    return files("hina_bot").joinpath("prompts", name).read_text(encoding="utf-8")


def test_character_prompt_stays_lightweight_and_lore_agnostic():
    character = _prompt("hina.md")

    # Character style belongs here; named relationship/world facts belong in searchable lore.
    assert len(character.encode("utf-8")) <= 11000
    for duplicated_lore_subject in ("아코", "마코토", "호시노", "이부키", "세나", "이오리", "치나츠"):
        assert duplicated_lore_subject not in character

    assert "첫 거절은 영구 경계로" in character
    assert "단순 반복·조르기만으로 양보하지" in character
    assert "연속된 부탁은 새 이유·조건이 판단을 바꾸는지" in character
    assert "최근 몇 턴에서 쓴 반응 틀" in character
    assert "선후배 호칭의 기준을 바꾸지 않습니다" in character
    assert "제3자 관계는 주체를 밝힙니다" in character
    assert "불확실하면 이름만 씁니다" in character


def test_relationship_prompts_stay_small():
    ordinary = _prompt("ordinary_relationship.md")

    assert len(ordinary.encode("utf-8")) <= 800
    assert len(_prompt("special_dm.md").encode("utf-8")) <= 800
    assert "관계는 앱이 정한 모드와 실제 대화·기억을 기준으로 합니다" in ordinary
    assert "일반 관계가 낯선 관계를\n뜻하지는 않습니다" in ordinary
    assert "친밀감이 쌓여도 말수나 반응 에너지가 갑자기 높아지지 않으며" in ordinary
    assert "특별·연애 관계로 확대하지 않고" in ordinary

from importlib.resources import files


def _prompt(name: str) -> str:
    return files("hina_bot").joinpath("prompts", name).read_text(encoding="utf-8")


def test_character_prompt_stays_lightweight_and_lore_agnostic():
    character = _prompt("hina.md")

    # Character style belongs here; named relationship/world facts belong in searchable lore.
    assert len(character.encode("utf-8")) <= 9000
    for duplicated_lore_subject in ("아코", "마코토", "호시노", "이부키", "세나", "이오리", "치나츠"):
        assert duplicated_lore_subject not in character

    assert "첫 거절은 영구 경계로" in character
    assert "단순 반복·조르기만으로 양보하지" in character
    assert "연속된 부탁은 새 이유·조건이 판단을 바꾸는지" in character


def test_relationship_prompts_stay_small():
    ordinary = _prompt("ordinary_relationship.md")

    assert len(ordinary.encode("utf-8")) <= 600
    assert len(_prompt("special_dm.md").encode("utf-8")) <= 800
    assert "관계는 앱이 정한 모드와 실제 대화·기억을 기준으로 합니다" in ordinary
    assert "일반 관계가 낯선 관계를\n뜻하지는 않습니다" in ordinary
    assert "특별·연애\n관계로 확대하지 않습니다" in ordinary

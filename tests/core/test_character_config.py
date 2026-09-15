from hina_bot.core.character import get_character_config
from hina_bot.core.knowledge_ingest import _row_score
from hina_bot.core.lore import LoreIndex
from hina_bot.core.runtime_knowledge import _terms as runtime_terms


def test_character_config_drives_retrieval_terms(monkeypatch):
    monkeypatch.setenv("CHARACTER_NAME", "텐도 아리스")
    monkeypatch.setenv("CHARACTER_ALIASES", "아리스,텐도 아리스")
    monkeypatch.setenv("CALL_PREFIXES", "아리스야")
    monkeypatch.setenv("CHARACTER_WORLD_TERMS", "밀레니엄,선생")
    monkeypatch.setenv("CHARACTER_EMOJI_PREFIXES", "aris,alice")

    character = get_character_config()

    assert character.name == "텐도 아리스"
    assert character.call_prefixes == ("아리스야",)
    assert character.emoji_prefixes == ("aris", "alice")
    assert "아리스" in character.search_stopwords
    assert "밀레니엄" in character.search_stopwords

    assert LoreIndex._terms("아리스야 밀레니엄 전투") == {"전투"}
    assert runtime_terms("아리스 밀레니엄 전투") == {"전투"}


def test_knowledge_candidate_scoring_ignores_character_only_match(monkeypatch):
    monkeypatch.setenv("CHARACTER_NAME", "텐도 아리스")
    monkeypatch.setenv("CHARACTER_ALIASES", "아리스,텐도 아리스")
    monkeypatch.setenv("CALL_PREFIXES", "아리스야")
    monkeypatch.setenv("CHARACTER_WORLD_TERMS", "밀레니엄")

    row = {
        "content": "아리스는 밀레니엄 소속이며 레일건을 사용한다.",
        "keywords": ["아리스", "레일건"],
        "subjects": ["아리스"],
    }

    assert _row_score("아리스", row) == 0
    assert _row_score("레일건 뭐 써?", row) > 0

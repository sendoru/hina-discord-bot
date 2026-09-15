from hina_bot.ai.information_intent import lore_query
from hina_bot.core.admin_db import AdminDatabase
from hina_bot.core.lore import LoreIndex
from hina_bot.core.runtime_knowledge import RuntimeKnowledgeRegistry


def test_configured_call_prefix_is_removed_before_lore_search():
    assert lore_query(
        "아리스야 안녕",
        profile=False,
        relation=False,
        call_prefixes=("아리스야",),
    ) == "안녕"


def test_short_profile_fields_match_compound_static_keywords():
    lore = LoreIndex.load()

    height = lore.search("키")
    hobby = lore.search("취미")

    assert any("142cm" in row.get("content", "") for row in height)
    assert any("수면과 휴식" in row.get("content", "") for row in hobby)


def test_short_fields_match_compound_runtime_keywords():
    database = AdminDatabase(":memory:")
    try:
        registry = RuntimeKnowledgeRegistry(database, kind="world_fact")
        registry.add(
            "profile.height",
            "테스트 캐릭터의 키는 150cm다.",
            "키 150cm",
            "테스트 캐릭터",
            "self",
            "프로필 상시 설정",
        )

        result = registry.search("키")

        assert result
        assert "150cm" in result[0]["content"]
    finally:
        database.close()

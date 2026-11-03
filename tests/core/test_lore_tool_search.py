"""Deterministic on-demand lore retrieval: recall, ranking, privacy boundaries."""

import pytest

from hina_bot.core.knowledge_retrieval import KnowledgeCandidate
from hina_bot.core.lore_tool_search import LoreKeywordIndex


def candidate(identifier, summary, *, subjects=(), keywords=(), **kwargs):
    return KnowledgeCandidate(
        candidate_id=identifier,
        source=kwargs.pop("source", "static_lore"),
        reference=identifier,
        kind=kwargs.pop("kind", "world_fact"),
        content=summary,
        search_text=summary,
        subjects=tuple(subjects),
        keywords=tuple(keywords),
        **kwargs,
    )


def test_alias_and_multiple_llm_queries_find_entries_without_required_entity_ids():
    index = LoreKeywordIndex([
        candidate("canon.admin", "행정관은 기록과 서류를 관리한다.",
                  subjects=["행정관"], keywords=["문서", "기록"],
                  source_metadata=(("title", "공식 기록"), ("locator", "2절"))),
        candidate("canon.guard", "경비대는 순찰과 경계를 담당한다.",
                  subjects=["경비대"], keywords=["순찰"]),
    ])
    result = index.search(["행정관 서류 업무", "문서 관리"])
    assert result["status"] == "ok"
    assert result["results"][0]["id"] == "canon.admin"
    assert result["results"][0]["source"]["title"] == "공식 기록"
    assert len({item["id"] for item in result["results"]}) == len(result["results"])


def test_korean_word_extension_matches_trigrams_without_morphological_analyzer():
    index = LoreKeywordIndex([
        candidate("canon.ledger", "학생부 기록을 정리한다.", subjects=["학생부"]),
        candidate("canon.food", "식당에서 식사를 한다.", subjects=["식당"]),
    ])
    assert index.search(["학생부원들의 기록"])["results"][0]["id"] == "canon.ledger"


def test_audience_only_and_reference_only_are_never_exposed():
    index = LoreKeywordIndex([
        candidate("canon.hidden", "기밀 회의 기록", subjects=["기밀"],
                  awareness="audience_only"),
        candidate("canon.adaptation", "기밀 외전 묘사", subjects=["기밀"],
                  fact_type="adaptation"),
        candidate("canon.visible", "기밀은 공개되지 않았다.", subjects=["기밀"],
                  awareness="unknown", fact_type="unknown",
                  kind="interpretation"),
    ])
    result = index.search(["기밀"])
    assert [r["id"] for r in result["results"]] == ["canon.visible"]
    assert result["results"][0]["awareness"] == "unknown"
    assert result["results"][0]["fact_type"] == "unknown"


def test_community_optional_and_dynamic_knowledge_is_live():
    index = LoreKeywordIndex([
        candidate("meme.joke", "이 장면은 유머로 반응한다.",
                  subjects=["장면"], kind="optional_reaction"),
    ])
    dynamic = [candidate("runtime.notes", "업무 노트는 책상에 있다.",
                         subjects=["업무 노트"], source="runtime_knowledge")]
    assert index.search(["장면"], include_community=False)["results"] == []
    assert index.search(["장면"], include_community=True)["results"][0]["id"] == "meme.joke"
    assert index.search(["업무 노트"], dynamic_candidates=dynamic)["results"][0]["id"] == "runtime.notes"


@pytest.mark.parametrize("queries", [[], ["x"], ["두 글자", "다른 검색", "세 번째", "네 번째"], [123], [" \n "]])
def test_invalid_queries_fail_closed(queries):
    with pytest.raises(ValueError):
        LoreKeywordIndex([]).search(queries)


def test_empty_match_and_budget_never_return_unrelated_or_oversized_rows():
    index = LoreKeywordIndex([
        candidate("canon.one", "고유 검색어에 대한 내용 " + "긴 문장 " * 500,
                  subjects=["고유 검색어"]),
    ])
    assert index.search(["불일치어"]) == {"status": "not_found", "results": []}
    assert index.search(["고유 검색어"], max_chars=50)["results"] == []
    with pytest.raises(ValueError):
        index.search(["고유 검색어"], limit=6)

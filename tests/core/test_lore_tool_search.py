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


def test_rare_subject_beats_common_name_in_rank():
    rows = [
        candidate("canon.generic." + str(i), "히나는 평범한 업무를 한다.",
                  subjects=("히나",), keywords=("히나",))
        for i in range(24)
    ]
    rows.append(candidate(
        "canon.special", "아코는 선도부 행정 업무를 담당한다.",
        subjects=("히나", "아코"), keywords=("아코", "행정 업무"),
    ))
    results = LoreKeywordIndex(rows).search(["히나 아코 행정 업무"])
    assert results["results"][0]["id"] == "canon.special"


def test_korean_particles_spacing_and_compounds_preserve_recall():
    index = LoreKeywordIndex([
        candidate("canon.school", "게헨나 학원의 선도부장이 담당한다.",
                  subjects=("게헨나 학원",), keywords=("선도부장",)),
        candidate("canon.staff", "아코는 선도부 행정관이다.",
                  subjects=("아코",), keywords=("선도부 행정관",)),
    ])
    assert index.search(["게 헨나 학원에서 선도 부장의 일"])[
        "results"][0]["id"] == "canon.school"
    assert index.search(["아코랑 선도부 행정관으로"])[
        "results"][0]["id"] == "canon.staff"


def test_multiple_queries_fuse_and_deduplicate_overlapping_candidates():
    index = LoreKeywordIndex([
        candidate("canon.alpha", "첫째 인물 설정", subjects=("첫째 인물",)),
        candidate("canon.beta", "둘째 인물 설정", subjects=("둘째 인물",)),
    ])
    values = index.search(["첫째 인물", "둘째 인물", "첫째 인물"])
    ids = [row["id"] for row in values["results"]]
    assert "canon.alpha" in ids and "canon.beta" in ids
    assert len(ids) == len(set(ids))


def test_real_packaged_lore_queries_retrieve_reviewed_entries():
    # Actual corpus, no new JSONL metadata/aliases and no embedding calls.
    from hina_bot.core.lore import LoreIndex

    index = LoreKeywordIndex(LoreIndex.load().candidates(include_community=False))
    queries = (
        (["아코 선도부 선임 행정관"], "canon.ako.role_in_gehenna_and_relationship_with_hina"),
        (["마코토 만마전 의장"], "canon.gehenna.makoto.position"),
        (["호시노 유메 죽음 사고"], "canon.hina.understands_yume_death_not_hoshino_fault_vol1_ch3"),
    )
    for text, expected in queries:
        results = index.search(text, limit=5)
        assert expected in [item["id"] for item in results["results"]], text


def test_unrelated_query_does_not_return_generic_gram_only_hits():
    index = LoreKeywordIndex([
        candidate("canon.one", "선도부는 공식 조직입니다.",
                  subjects=("선도부",)),
    ])
    assert index.search(["기하학의 다변수 적분"])["results"] == []


def test_total_serialized_budget_includes_response_envelope():
    index = LoreKeywordIndex([
        candidate("canon.one", "기록", subjects=("기록",)),
    ])
    response = index.search(["기록"], max_chars=150)
    assert len(__import__("json").dumps(
        response, ensure_ascii=False, separators=(",", ":"),
    )) <= 150

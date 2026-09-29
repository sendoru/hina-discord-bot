from hina_bot.core.knowledge_retrieval import (
    KnowledgeCandidate,
    lexical_search,
    rank_lexical_candidates,
)


def candidate(identifier, *, subject_boundary=False, content="내용", keywords=(), subjects=()):
    return KnowledgeCandidate(
        candidate_id=identifier,
        source="test",
        reference=identifier,
        kind="world_fact",
        content=content,
        search_text=content,
        subjects=tuple(subjects),
        keywords=tuple(keywords),
        awareness="public_knowledge",
        time="상시",
        subject_boundary=subject_boundary,
    )


def test_exact_subject_and_keyword_scoring_preserve_order():
    rows = [
        candidate("second", keywords=("행정관",)),
        candidate("first", subjects=("아코",), keywords=("행정관",)),
    ]

    ranked = rank_lexical_candidates("아코는 행정관이야?", rows)

    assert [row.candidate.candidate_id for row in ranked] == ["first", "second"]
    assert ranked[0].score > ranked[1].score


def test_subject_boundary_can_reject_embedded_static_name():
    loose = candidate("loose", subjects=("아코",))
    strict = candidate("strict", subjects=("아코",), subject_boundary=True)

    assert lexical_search("초아코형", [loose], limit=1, chars=1000)
    assert lexical_search("초아코형", [strict], limit=1, chars=1000) == []


def test_reference_serialization_and_budget_are_shared():
    row = KnowledgeCandidate(
        candidate_id="meme.test",
        source="test",
        kind="optional_reaction",
        content="짧게 반응한다.",
        search_text="머리 크기 농담",
        subjects=("히나",),
        keywords=("머리 크기",),
    )

    result = lexical_search("머리 크기", [row], limit=1, chars=1000)

    assert result == [{"kind": "optional_reaction", "content": "짧게 반응한다."}]
    assert lexical_search("머리 크기", [row], limit=1, chars=1) == []

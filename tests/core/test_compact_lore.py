"""Compact lore context contract: boundaries, source minimization and stable output."""

from hina_bot.core.compact_lore import compile_compact_lore
from hina_bot.core.lore import LoreIndex


def row(identifier, summary, *, knowledge="self", fact="fact_direct", lane="canon"):
    return {
        "id": identifier, "summary": summary, "knowledge": knowledge,
        "fact_type": fact, "lane": lane, "status": "accepted",
        "timeline": "프로필 상시 설정", "source": {"title": "원문", "url": "example"},
        "keywords": ["검색 키워드"], "reaction": "놀랐어!",
    }


def test_full_context_preserves_cognition_and_epistemic_boundaries():
    rows = [
        row("a.fact", "직접 아는 사실", knowledge="direct_experience"),
        row("b.report", "들었다는 이야기", knowledge="reported", fact="fact_reported"),
        row("c.inference", "추측에 불과함", knowledge="inference", fact="inference"),
        row("d.unknown", "공식적으로 확인되지 않음", knowledge="unknown", fact="unknown"),
        row("e.audience", "플레이어만 아는 비밀", knowledge="audience_only"),
        row("f.fandom", "팬덤 해석", fact="fandom"),
        row("g.community", "농담", lane="community_meme"),
        {**row("h.unaccepted", "승인되지 않음"), "status": "candidate"},
    ]
    lore = LoreIndex(rows)
    result = compile_compact_lore(lore)
    assert result.items == 5
    assert result.excluded_audience_items == 1
    assert "직접 아는 사실" in result.text
    assert "전해 들음; 발언/전언" in result.text
    assert "추정" in result.text and "미확정" in result.text
    assert "플레이어만 아는 비밀" not in result.text
    assert "팬덤 해석" not in result.text
    assert "승인되지 않음" not in result.text
    assert "팬덤 반응 가이드 / 사실 아님" in result.text
    assert "검색 키워드" not in result.text
    assert "example" not in result.text

    disabled = compile_compact_lore(lore, include_community=False)
    assert disabled.items == 4
    assert "놀랐어!" not in disabled.text
    assert disabled.text == compile_compact_lore(lore, include_community=False).text


def test_packaged_lore_compiles_without_retrieval():
    result = compile_compact_lore(LoreIndex.load(), include_community=False)
    assert result.items > 100
    assert result.chars == len(result.text)
    assert "소라사키 히나는" in result.text
    assert "발언/전언" in result.text

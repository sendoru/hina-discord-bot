from dataclasses import replace
from pathlib import Path

import pytest

from hina_bot.core.knowledge_retrieval import KnowledgeCandidate, KnowledgeUsage
from hina_bot.core.lore import LoreIndex, read_jsonl
from hina_bot.core.semantic_retrieval import EmbeddingResult, EmbeddingUsage
from hina_bot.tooling.ambient_calibration import evaluate, main, validate_cases


class AmbientGeometry:
    cache_key = "ambient-test:v1:2"
    dimensions = 2

    def __init__(self):
        self.documents = []
        self.queries = []

    async def embed_candidates(self, texts):
        self.documents.extend(texts)
        vectors = {
            "positive": (1, 0),
            "hard": (0.4, 0.916515138991168),
            "other": (0, 1),
        }
        return EmbeddingResult(
            tuple(vectors[text] for text in texts),
            EmbeddingUsage(60, 1),
        )

    async def embed_query(self, text):
        self.queries.append(text)
        vector = (1, 0) if "평가" not in text else (0.6, 0.8)
        return EmbeddingResult((vector,), EmbeddingUsage(15, 1))


def candidate(identifier):
    return KnowledgeCandidate(
        candidate_id=identifier,
        source="static_lore",
        kind="interpretation",
        content=identifier,
        search_text=identifier,
        subjects=("히나",),
        keywords=(),
        entities=("character.hina",),
        usages=(KnowledgeUsage.FACTUAL, KnowledgeUsage.AMBIENT),
        lane="canon",
        fact_type="inference",
        awareness="inference",
        semantic_text=identifier,
        evidence_ids=("canon.evidence",),
    )


def small_fixture():
    rows = [candidate(identifier) for identifier in ("positive", "hard", "other")]
    cases = [
        {
            "id": "calibration",
            "split": "calibration",
            "visible_text": "보정 장면",
            "anchor_text": "",
            "recent_same_speaker": [],
            "relationship_axes": {},
            "positive": ["positive"],
            "hard_negative": ["hard"],
            "unrelated": ["other"],
        },
        {
            "id": "evaluation",
            "split": "evaluation",
            "visible_text": "평가 장면",
            "anchor_text": "",
            "recent_same_speaker": [],
            "relationship_axes": {},
            "positive": ["positive"],
            "hard_negative": ["hard"],
            "unrelated": ["other"],
        },
    ]
    return cases, rows


async def test_report_measures_ambient_scene_usage_and_selection():
    cases, rows = small_fixture()
    backend = AmbientGeometry()
    report = await evaluate(
        backend,
        cases,
        rows,
        min_score=0.75,
        usd_per_million_tokens=2.0,
    )
    assert report["status"] == "experimental_not_production_calibration"
    assert backend.documents == ["positive", "hard", "other"]
    assert len(backend.queries) == 2
    assert report["trial_thresholds"]["reject"] == pytest.approx(0.4)
    assert report["candidate_warmup"]["usage"]["prompt_token_count"] == 60
    assert report["usage"]["total"] == {
        "prompt_token_count": 90,
        "request_count": 3,
    }
    assert report["cost"]["estimated_usd"] == pytest.approx(90 * 2 / 1_000_000)
    first = report["cases"][0]
    assert first["selected"] == ["positive"]
    assert first["positive_hits"] == 1
    assert first["semantic_query_character_count"] > 0
    assert first["query_embedding"]["prompt_token_count"] == 15


def test_packaged_ambient_fixture_validation(capsys):
    assert main(["--validate-only"]) == 0
    out = capsys.readouterr().out
    assert '"ambient_corpus_size": 5' in out
    cases = read_jsonl(Path("evals/retrieval_v2_ambient.jsonl"))
    rows = LoreIndex.load().candidates(include_community=False)
    ambient = [
        row for row in rows
        if KnowledgeUsage.AMBIENT in row.retrieval_usages
    ]
    validate_cases(cases, ambient)
    assert {case["split"] for case in cases} == {"calibration", "evaluation"}


def test_invalid_fixture_and_missing_key_fail_before_network(monkeypatch):
    cases, rows = small_fixture()
    cases[0]["hard_negative"].append("positive")
    with pytest.raises(ValueError):
        validate_cases(cases, rows)

    cases, rows = small_fixture()
    with pytest.raises(ValueError):
        validate_cases(cases, [*rows, replace(rows[0])])

    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    with pytest.raises(SystemExit) as error:
        main([])
    assert error.value.code == 2
    with pytest.raises(SystemExit):
        main(["--validate-only", "--reject", "0.5"])


async def test_missing_usage_prevents_cost_estimate():
    class MissingUsage(AmbientGeometry):
        async def embed_query(self, text):
            result = await super().embed_query(text)
            return replace(result, usage=EmbeddingUsage(None, 1))

    cases, rows = small_fixture()
    report = await evaluate(
        MissingUsage(),
        cases,
        rows,
        usd_per_million_tokens=1.0,
    )
    assert report["usage"]["total"]["prompt_token_count"] is None
    assert report["cost"]["estimated_usd"] is None

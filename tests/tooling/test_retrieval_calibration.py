from dataclasses import replace
from math import sqrt
from pathlib import Path

import pytest

from hina_bot.core.knowledge_retrieval import KnowledgeCandidate
from hina_bot.core.lore import LoreIndex, read_jsonl
from hina_bot.core.semantic_retrieval import EmbeddingResult, EmbeddingUsage
from hina_bot.tooling.retrieval_calibration import evaluate, main, validate_cases


class MeasuredGeometry:
    cache_key = "deterministic-test:2:v1"
    dimensions = 2

    def __init__(self):
        self.documents = []
        self.queries = []

    async def embed_candidates(self, texts):
        self.documents.extend(texts)
        vectors = {"positive": (1, 0), "hard": (.4, sqrt(.84)), "other": (0, 1)}
        return EmbeddingResult(tuple(vectors[text] for text in texts), EmbeddingUsage(73, 1))

    async def embed_query(self, text):
        self.queries.append(text)
        return EmbeddingResult(((1, 0) if "평가" not in text else (.6, .8),),
                               EmbeddingUsage(17, 1))


def small_fixture():
    rows = [KnowledgeCandidate(candidate_id=text, source="static_lore", kind="world_fact",
                               content=text, search_text=text, subjects=(), keywords=())
            for text in ("positive", "hard", "other")]
    cases = [{"id": split, "split": split, "visible_text": query, "intent": "fact",
              "positive": ["positive"], "hard_negative": ["hard"], "unrelated": ["other"]}
             for split, query in (("calibration", "보정"), ("evaluation", "평가"))]
    return cases, rows


async def test_report_uses_training_only_and_reuses_embeddings_across_fusions_and_variants():
    cases, rows = small_fixture()
    backend = MeasuredGeometry()
    result = await evaluate(backend, cases, rows, compare_intent_hint=True,
                            usd_per_million_tokens=2.0)
    assert result["status"] == "experimental_not_production_calibration"
    assert backend.documents == ["positive", "hard", "other"]
    assert len(backend.queries) == 4  # 2 cases x 2 query variants, not x fusion methods
    variant = result["variants"]["conversational_text"]
    assert variant["trial_thresholds"]["reject"] == pytest.approx(.4)
    assert variant["distributions"]["evaluation"]["hard_negative"]["max"] > .9
    assert set(variant["cases"][0]["fusion"]) == {"lexical_only", "rrf", "weighted", "lexical_first"}
    assert variant["cases"][0]["fusion"]["lexical_only"]["positive_hits"] == 0
    assert variant["cases"][0]["fusion"]["lexical_first"]["positive_hits"] == 1
    assert result["candidate_warmup"]["usage"]["prompt_token_count"] == 73
    assert result["candidate_warmup"]["elapsed_ms"] >= 0
    assert result["usage"]["total"] == {"prompt_token_count": 73 + 4 * 17, "request_count": 5}
    assert result["cost"]["estimated_usd"] == pytest.approx((73 + 4 * 17) * 2 / 1_000_000)
    first = variant["cases"][0]
    assert first["semantic_query_character_count"] == 2
    assert first["query_embedding"]["prompt_token_count"] == 17
    assert first["query_embedding"]["latency_ms"] >= 0
    assert first["candidate_embedding"]["prompt_token_count"] == 0
    assert first["request_count"] == 1


def test_packaged_fixture_validation(capsys):
    assert main(["--validate-only"]) == 0
    assert '"corpus_size": 130' in capsys.readouterr().out
    cases = read_jsonl(Path("evals/retrieval_v2_calibration.jsonl"))
    rows = LoreIndex.load().candidates(include_community=False)
    validate_cases(cases, rows)
    assert {c["split"] for c in cases} == {"calibration", "evaluation"}


async def test_measurement_failure_is_not_replaced_by_lexical_or_fake_scores():
    class Unavailable(MeasuredGeometry):
        async def embed_query(self, text):
            raise RuntimeError("provider unavailable")
    cases, rows = small_fixture()
    with pytest.raises(RuntimeError):
        await evaluate(Unavailable(), cases, rows)


def test_missing_key_and_invalid_args_fail_before_network(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    with pytest.raises(SystemExit) as error:
        main([])
    assert error.value.code == 2
    with pytest.raises(SystemExit):
        main(["--validate-only", "--reject", "0.5"])


def test_fixture_overlapping_labels_and_duplicate_ids_rejected():
    cases, rows = small_fixture()
    cases[0]["hard_negative"].append("positive")
    with pytest.raises(ValueError):
        validate_cases(cases, rows)
    cases, rows = small_fixture()
    with pytest.raises(ValueError):
        validate_cases(cases, [*rows, replace(rows[0])])


async def test_report_keeps_lexical_and_semantic_representations_separate():
    cases, rows = small_fixture()
    cases[0]["lexical_query"] = "직접 만남 대면 대화 관계 접점"
    cases[0]["expected_semantic_query"] = cases[0]["visible_text"]
    backend = MeasuredGeometry()
    report = await evaluate(backend, cases, rows)
    representations = report["variants"]["conversational_text"]["cases"][0]["representations"]
    assert representations == {"lexical": cases[0]["lexical_query"], "semantic": "보정"}
    assert backend.queries[0] == "보정"


async def test_missing_usage_prevents_total_cost_estimate():
    class MissingUsage(MeasuredGeometry):
        async def embed_query(self, text):
            result = await super().embed_query(text)
            return replace(result, usage=EmbeddingUsage(None, 1))
    cases, rows = small_fixture()
    report = await evaluate(MissingUsage(), cases, rows, usd_per_million_tokens=1)
    assert report["usage"]["total"]["prompt_token_count"] is None
    assert report["usage"]["total"]["request_count"] == 3
    assert report["cost"]["estimated_usd"] is None

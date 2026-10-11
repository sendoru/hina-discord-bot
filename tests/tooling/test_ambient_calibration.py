import json
from pathlib import Path

from hina_bot.core.semantic_retrieval import EmbeddingResult, EmbeddingUsage
from hina_bot.tooling.ambient_calibration import evaluate, main


class FakeAmbientBackend:
    cache_key = "fake:ambient:v1:5"
    dimensions = 5

    def __init__(self):
        self.candidate_calls = 0
        self.query_calls = 0

    async def embed_candidates(self, texts):
        self.candidate_calls += 1
        vectors = []
        for index, _text in enumerate(texts):
            vector = [0.0] * self.dimensions
            vector[index % self.dimensions] = 1.0
            vectors.append(tuple(vector))
        return EmbeddingResult(
            tuple(vectors),
            EmbeddingUsage(prompt_token_count=50, request_count=1),
        )

    async def embed_query(self, text):
        self.query_calls += 1
        # Deterministic and content-independent: this test validates report accounting,
        # not model quality or production threshold values.
        return EmbeddingResult(
            ((1.0, 0.0, 0.0, 0.0, 0.0),),
            EmbeddingUsage(prompt_token_count=10, request_count=1),
        )


def test_packaged_ambient_calibration_fixtures_validate_without_api_key(capsys):
    assert main(["--validate-only"]) == 0
    row = json.loads(capsys.readouterr().out)
    assert row["valid"] is True
    assert row["cases"] >= 7
    assert row["ambient_corpus_size"] == 5


async def test_ambient_calibration_report_accounts_for_query_and_candidate_usage():
    from hina_bot.core.lore import read_jsonl

    cases = read_jsonl(Path("evals/retrieval_v2_ambient.jsonl"))
    backend = FakeAmbientBackend()
    report = await evaluate(
        backend,
        cases,
        reject=0.2,
        strong=0.9,
        usd_per_million_tokens=2.0,
    )

    assert report["status"] == "experimental_not_production_calibration"
    assert report["backend_key"] == backend.cache_key
    assert report["corpus_size"] == 5
    assert report["fixture_count"] == len(cases)
    assert backend.candidate_calls == 1
    assert backend.query_calls == len(cases)
    assert report["usage"]["candidate"]["prompt_token_count"] == 50
    assert report["usage"]["query"]["prompt_token_count"] == 10 * len(cases)
    assert report["usage"]["total"]["request_count"] == 1 + len(cases)
    assert report["cost"]["price_source"] == "operator_supplied"
    assert report["cases"][0]["semantic_query_character_count"] > 0

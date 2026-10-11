import json
from dataclasses import replace

import httpx
import pytest

from hina_bot.ai.embedding_backend import (
    EmbeddingError,
    GeminiEmbeddingBackend,
    GeminiEmbeddingConfig,
)


async def test_gemini_protocol_batch_order_dimensions_and_usage():
    calls = []

    def respond(request):
        payload = json.loads(request.content)
        calls.append((request, payload))
        if request.url.path.endswith(":batchEmbedContents"):
            return httpx.Response(200, json={"embeddings": [{"values": [3, 4]}, {"values": [4, 3]}],
                                            "usageMetadata": {"promptTokenCount": 71}})
        return httpx.Response(200, json={"embedding": {"values": [1, 0]},
                                        "usageMetadata": {"promptTokenCount": 19}})

    backend = GeminiEmbeddingBackend("secret", config=GeminiEmbeddingConfig(dimensions=2),
                                     transport=httpx.MockTransport(respond))
    try:
        result = await backend.embed_candidates(["document one", "document two"])
        query = await backend.embed_query("query")
        assert result.vectors == ((0.6, 0.8), (0.8, 0.6)) and query.vectors == ((1, 0),)
        assert result.usage.prompt_token_count == 71 and result.usage.request_count == 1
        assert query.usage.prompt_token_count == 19 and query.usage.request_count == 1
        assert len(calls) == 2
        assert calls[0][0].url.path.endswith(":batchEmbedContents")
        assert calls[1][0].url.path.endswith(":embedContent")
        requests = calls[0][1]["requests"]
        for payload in [*requests, calls[1][1]]:
            assert payload["model"] == "models/gemini-embedding-2"
            assert payload["outputDimensionality"] == 2
            assert "taskType" not in payload and len(payload["content"]["parts"]) == 1
        assert requests[0]["content"]["parts"][0]["text"].endswith("document one")
        assert requests[1]["content"]["parts"][0]["text"].endswith("document two")
        assert calls[1][1]["content"]["parts"][0]["text"].endswith("query")
        assert all(req.headers["x-goog-api-key"] == "secret" for req, _ in calls)
    finally:
        await backend.close()


@pytest.mark.parametrize("status,payload", [(429, {"error": "sensitive content"}),
                                          (200, {}), (200, {"embedding": {"values": [0, 0]}})])
async def test_error_messages_do_not_include_provider_content(status, payload):
    backend = GeminiEmbeddingBackend("secret", config=GeminiEmbeddingConfig(dimensions=2),
                                     transport=httpx.MockTransport(
                                         lambda _: httpx.Response(status, json=payload)))
    try:
        with pytest.raises(EmbeddingError) as error:
            await backend.embed_query("private query")
        assert "sensitive" not in str(error.value) and "private" not in str(error.value)
    finally:
        await backend.close()


def test_defaults_and_cache_identity_changes():
    config = GeminiEmbeddingConfig()
    assert config.model == "gemini-embedding-2" and config.dimensions == 768
    with pytest.raises(ValueError):
        GeminiEmbeddingConfig(model="gemini-embedding-001")
    assert replace(config, dimensions=1536) != config


async def test_cache_key_changes_for_model_dimensions_revision():
    base = GeminiEmbeddingConfig()
    configs = [base, replace(base, dimensions=1536), replace(base, revision="2"),
               replace(base, model="gemini-embedding-2-preview")]
    backends = [GeminiEmbeddingBackend("secret", config=c, transport=httpx.MockTransport(
        lambda _: httpx.Response(200))) for c in configs]
    try:
        assert len({b.cache_key for b in backends}) == 4
    finally:
        for backend in backends:
            await backend.close()


@pytest.mark.parametrize("batch_size", [1, 50, 100])
async def test_bounded_batches_preserve_order_and_sum_usage(batch_size):
    sizes = []

    def respond(request):
        requests = json.loads(request.content)["requests"]
        sizes.append(len(requests))
        vectors = [{"values": [int(r["content"]["parts"][0]["text"].rsplit(" ", 1)[1]) + 1, 1]}
                   for r in requests]
        return httpx.Response(200, json={"embeddings": vectors,
                                        "usageMetadata": {"promptTokenCount": 7}})

    backend = GeminiEmbeddingBackend(
        "secret", config=GeminiEmbeddingConfig(dimensions=2, batch_size=batch_size),
        transport=httpx.MockTransport(respond),
    )
    try:
        result = await backend.embed_candidates([str(i) for i in range(101)])
        assert all(1 <= size <= batch_size for size in sizes)
        assert sum(sizes) == 101 and result.usage.request_count == len(sizes)
        assert result.usage.prompt_token_count == 7 * len(sizes)
        assert len(result.vectors) == 101
        assert [v[0] / v[1] for v in result.vectors] == pytest.approx(range(1, 102))
        empty = await backend.embed_candidates([])
        assert empty.usage.request_count == 0 and not empty.vectors
    finally:
        await backend.close()


@pytest.mark.parametrize("bad", [[], [{"values": [1]}], [{"values": [0, 0]}],
                                 [{"values": [1, 0]}, {"values": [1, 0]}]])
async def test_later_invalid_batch_cannot_partially_commit_cache(bad):
    from hina_bot.core.knowledge_retrieval import KnowledgeCandidate
    from hina_bot.core.semantic_retrieval import SemanticIndex

    calls = []

    def respond(request):
        calls.append(request)
        return httpx.Response(200, json={
            "embeddings": [{"values": [1, 0]}] if len(calls) == 1 else bad,
            "usageMetadata": {"promptTokenCount": 5},
        })

    backend = GeminiEmbeddingBackend("secret", config=GeminiEmbeddingConfig(dimensions=2, batch_size=1),
                                     transport=httpx.MockTransport(respond))
    index = SemanticIndex(backend)
    rows = [KnowledgeCandidate(candidate_id=str(i), source="static_lore", kind="world_fact",
                               content=str(i), search_text=str(i), subjects=(), keywords=())
            for i in range(2)]
    try:
        with pytest.raises(EmbeddingError):
            await index.warm(rows)
        assert len(calls) == 2 and not index._cache
    finally:
        await backend.close()


async def test_missing_usage_is_unknown_not_zero_in_query_and_batch_totals():
    def respond(request):
        data = ({"embeddings": [{"values": [1, 0]}]}
                if request.url.path.endswith(":batchEmbedContents")
                else {"embedding": {"values": [1, 0]}})
        return httpx.Response(200, json=data)

    backend = GeminiEmbeddingBackend("secret", config=GeminiEmbeddingConfig(dimensions=2, batch_size=1),
                                     transport=httpx.MockTransport(respond))
    try:
        batch = await backend.embed_candidates(["a", "b"])
        query = await backend.embed_query("q")
        assert batch.usage.prompt_token_count is None and batch.usage.request_count == 2
        assert query.usage.prompt_token_count is None and query.usage.request_count == 1
    finally:
        await backend.close()


@pytest.mark.parametrize("value", [-1, "10", True, 1.5])
async def test_invalid_usage_is_rejected(value):
    backend = GeminiEmbeddingBackend(
        "secret", config=GeminiEmbeddingConfig(dimensions=2),
        transport=httpx.MockTransport(lambda _: httpx.Response(200, json={
            "embedding": {"values": [1, 0]}, "usageMetadata": {"promptTokenCount": value},
        })),
    )
    try:
        with pytest.raises(EmbeddingError):
            await backend.embed_query("q")
    finally:
        await backend.close()


@pytest.mark.parametrize("value", [0, 101, 1.5, True])
def test_invalid_batch_limits(value):
    with pytest.raises(ValueError):
        GeminiEmbeddingConfig(batch_size=value)

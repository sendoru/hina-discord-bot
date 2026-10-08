import json
from dataclasses import replace

import httpx
import pytest

from hina_bot.ai.embedding_backend import (
    EmbeddingError,
    GeminiEmbeddingBackend,
    GeminiEmbeddingConfig,
)


async def test_gemini_protocol_distinct_documents_dimensions_and_normalization():
    calls = []

    def respond(request):
        calls.append((request, json.loads(request.content)))
        return httpx.Response(200, json={"embedding": {"values": [3, 4]}})

    backend = GeminiEmbeddingBackend("secret", config=GeminiEmbeddingConfig(dimensions=2),
                                     transport=httpx.MockTransport(respond))
    try:
        result = await backend.embed_candidates(["document one", "document two"])
        query = await backend.embed_query("query")
        assert result == [(0.6, 0.8)] * 2 and query == (0.6, 0.8)
        assert len(calls) == 3
        for request, payload in calls:
            assert request.url.path.endswith("models/gemini-embedding-2:embedContent")
            assert request.headers["x-goog-api-key"] == "secret"
            assert payload["outputDimensionality"] == 2
            assert "taskType" not in payload and len(payload["content"]["parts"]) == 1
        assert calls[0][1]["content"] != calls[1][1]["content"]
        assert calls[2][1]["content"] != calls[0][1]["content"]
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

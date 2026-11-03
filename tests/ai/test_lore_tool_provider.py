"""Mock-HTTP verification of search_lore across both Gemini tool APIs.

No live API calls or secrets. In particular, a tool-only model response must
not trigger the generic Gemini empty-text retry.
"""

import json
from types import SimpleNamespace as NS

import httpx
import pytest

from hina_bot.ai.local_tools import LocalToolExecutor
from hina_bot.ai.lore_tool import attach_lore_search
from hina_bot.ai.providers import _GeminiResponses
from hina_bot.ai.usage import UsageLogger
from hina_bot.core.knowledge_retrieval import KnowledgeCandidate
from hina_bot.core.lore_tool_search import LoreKeywordIndex


@pytest.mark.asyncio
@pytest.mark.parametrize("api", ["interactions", "generate_content"])
async def test_search_lore_mock_gemini_tool_roundtrip_with_search_and_url_context(
    tmp_path, api,
):
    documents = [KnowledgeCandidate(
        candidate_id="canon.staff", source="static_lore", reference="canon.staff",
        kind="world_fact", content="행정관은 서류를 관리한다.",
        search_text="행정관은 서류를 관리한다.",
        subjects=("행정관",), keywords=("서류",), awareness="public_knowledge",
    )]
    registry = attach_lore_search(
        None, LoreKeywordIndex(documents), lambda: (),
        include_community=False, event=lambda *_args, **_kwargs: None,
    )
    payloads = []

    async def handler(request: httpx.Request):
        payloads.append((str(request.url), json.loads(request.content)))
        first = len(payloads) == 1
        if api == "interactions":
            if first:
                return httpx.Response(200, json={
                    "id": "int_1", "status": "requires_action",
                    "steps": [{
                        "type": "function_call", "id": "call_lore",
                        "name": "search_lore",
                        "arguments": {"queries": ["행정관 서류"]},
                    }], "usage": {},
                })
            return httpx.Response(200, json={
                "id": "int_2", "status": "completed",
                "steps": [{
                    "type": "model_output",
                    "content": [{"type": "text", "text": "서류를 관리해."}],
                }], "usage": {},
            })
        if first:
            return httpx.Response(200, json={
                "candidates": [{"content": {"parts": [{
                    "functionCall": {
                        "id": "call_lore", "name": "search_lore",
                        "args": {"queries": ["행정관 서류"]},
                    },
                }]}}],
                "usageMetadata": {},
            })
        return httpx.Response(200, json={
            "candidates": [{"content": {"parts": [{"text": "서류를 관리해."}]}}],
            "usageMetadata": {},
        })

    transport = httpx.MockTransport(handler)
    http = httpx.AsyncClient(transport=transport)
    logger = UsageLogger(str(tmp_path / "gemini-lore.jsonl"))
    client = NS(
        provider_name="gemini",
        responses=_GeminiResponses(
            http, settings=NS(gemini_search_api_mode=api),
        ),
    )
    request = {
        "model": "gemini-test",
        "input": [{"role": "user", "content": "행정관은 무슨 일을 해?"}],
        "tools": [{"type": "web_search"}, {"type": "url_context"}, *registry.schemas()],
        "store": True,
    }
    try:
        async def send(data):
            return await logger.request(client, "answer", **data)

        result = await LocalToolExecutor(registry).run(send, request)
    finally:
        logger.close()
        await http.aclose()

    assert result.local_calls == 1
    assert result.response.output_text == "서류를 관리해."
    assert len(payloads) == 2  # no accidental extra retry of function-only output
    assert all(("interactions" in url) == (api == "interactions")
               for url, _ in payloads)
    first_payload, second_payload = (row[1] for row in payloads)
    if api == "interactions":
        assert {"type": "google_search"} in first_payload["tools"]
        assert {"type": "url_context"} in first_payload["tools"]
        assert any(row.get("name") == "search_lore" for row in first_payload["tools"])
        assert second_payload["previous_interaction_id"] == "int_1"
        assert second_payload["input"][0]["type"] == "function_result"
        assert second_payload["input"][0]["name"] == "search_lore"
    else:
        assert any("function_declarations" in row for row in first_payload["tools"])
        assert any("google_search" in row for row in first_payload["tools"])
        assert any("url_context" in row for row in first_payload["tools"])
        assert "functionResponse" in second_payload["contents"][-1]["parts"][0]
    # The model sees the actual retrieved record, not just an acknowledgement.
    assert "canon.staff" in json.dumps(second_payload, ensure_ascii=False)
    events = [
        json.loads(line) for line in
        (tmp_path / "gemini-lore.jsonl").read_text().splitlines()
    ]
    assert len([row for row in events if row.get("operation") == "answer"]) == 2
    assert all(row.get("empty_response_retries") is None for row in events)

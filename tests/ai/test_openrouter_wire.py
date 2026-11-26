import json
from types import SimpleNamespace as NS

import httpx
import pytest

from hina_bot.ai.audio import CURRENT_AUDIO_INPUTS, AudioInput
from hina_bot.ai.local_tools import LocalToolExecutor, LocalToolRegistry, LocalToolSpec
from hina_bot.ai.model_routing import fixed_model_plan
from hina_bot.ai.openrouter_capabilities import catalog_model_slug, parameter_support
from hina_bot.ai.output_repair import OutputRepair
from hina_bot.ai.providers import OpenRouterClient, ProviderAPIError
from hina_bot.ai.runtime_llm import LLM
from hina_bot.ai.semantic_model_routing import SemanticModelRouter
from hina_bot.ai.vision import CURRENT_VISUAL_INPUTS, VisualInput
from hina_bot.core.config import Settings
from hina_bot.core.knowledge_ingest import KnowledgeIngestor
from hina_bot.core.routing import Scope
from hina_bot.core.store import Store

MODEL = "fixture/model"


@pytest.fixture
def wire(monkeypatch):
    posts, gets, outputs = [], [], []

    async def transport(self, req):
        assert req.url.host == "openrouter.ai"
        if req.method == "GET":
            if req.url.path == "/api/v1/models":
                assert req.url.params["q"] == MODEL and "authorization" not in req.headers
                return httpx.Response(200, json={"data": [{"id": MODEL, "reasoning": {
                    "mandatory": False, "supported_efforts": ["low", "high"],
                }}]})
            assert req.url.path == "/api/v1/models/fixture/model/endpoints"
            assert "Authorization" not in req.headers
            gets.append(req.url)
            return httpx.Response(200, json={"data": {
                "id": MODEL, "architecture": {"input_modalities": ["text", "image", "audio"]},
                "endpoints": [{"tag": "fixture", "provider_name": "Fixture", "supported_parameters": [
                    "reasoning", "structured_outputs", "tools",
                ]}],
            }})
        assert req.url.path == "/api/v1/responses" and req.method == "POST"
        assert req.headers["X-OpenRouter-Metadata"] == "enabled"
        body = json.loads(req.content)
        posts.append(body)
        result = outputs.pop(0) if outputs else "hello"
        if result == "fixture timeout":
            raise httpx.ReadTimeout("fixture timeout", request=req)
        if type(result) is int:
            return httpx.Response(result, json={"error": {"message": "private fixture text"}})
        output = result if isinstance(result, list) else [{
            "type": "message", "id": "msg", "role": "assistant", "status": "completed",
            "content": [{"type": "output_text", "text": result, "annotations": []}],
        }]
        return httpx.Response(200, json={
            "id": "gen-fixture", "created_at": 1, "object": "response", "model": "fixture/returned",
            "openrouter_metadata": {"endpoints": {"available": [{"provider": "Fixture", "selected": True}]}},
            "status": "completed", "output": output, "usage": {
                "cost": 0, "input_tokens": 12, "output_tokens": 5, "total_tokens": 17,
                "input_tokens_details": {"cache_write_tokens": 0, "cached_tokens": 3},
                "output_tokens_details": {"reasoning_tokens": 2},
            },
        })
    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", transport)
    return posts, gets, outputs


async def test_all_operations(wire, tmp_path):
    posts, gets, outputs = wire
    preferences = {"only": ["fixture"], "order": ["fixture"], "allow_fallbacks": False,
                   "require_parameters": True, "data_collection": "deny", "zdr": True,
                   "ignore": ["slow"], "sort": {"by": "price", "partition": "none"},
                   "preferred_max_latency": {"p90": 3, "p99": 8},
                   "preferred_min_throughput": {"p90": 50},
                   "max_price": {"prompt": 0.1, "completion": 0.6}}
    path = tmp_path / "usage.jsonl"
    settings = Settings(discord_token="fixture", provider="openrouter", model=MODEL, fast_model=MODEL,
                        openrouter_provider_preferences=preferences, openrouter_affinity_secret="fixture",
                        openrouter_reasoning_effort="none", routing_classifier_provider="openrouter",
                        routing_classifier_model=MODEL, chat_web_search=False, usage_log_path=str(path))
    client = OpenRouterClient("fixture", settings=settings)
    store = Store.create(":memory:")
    bot = LLM(settings, client=client)
    try:
        await bot.answer(store, Scope(None, 123456, 456789), "fixture", "hello")
        await bot._memory_request("summarize_shared", "fixture", {}, fixed_model_plan(settings))
        router = SemanticModelRouter(settings, client, bot.usage)
        info = NS(routing=NS(visible_content="hi", prior_user_request="", anchor_source="", anchor=""),
                  references=(), search_reason="fixture", search_baseline_mode="none", search_mode="none", search_locked=True)
        outputs.append(json.dumps({"level": "low", "codes": ["simple_chat"], "uncertain": False,
                                   "web_need": "none", "web_codes": ["stable_or_contextual"],
                                   "web_uncertain": False}))
        await router.classify(info)
        await OutputRepair(settings, client, bot.usage)._repair_once("fixture", "conservative")
        outputs.append('{"items":[]}')
        await KnowledgeIngestor(bot).ingest("fixture")
    finally:
        await bot.close()
        store.close()
    assert len(posts) == 5 and len(gets) == 1
    assert len(posts[0]["session_id"]) == 64 and "session_id" not in posts[1]
    assert all(p["model"] == MODEL and p["provider"] == preferences and p["store"] is False for p in posts)
    assert all(p["reasoning"] == {"enabled": False} and p["max_output_tokens"] > 0 for p in posts)
    assert posts[-1]["text"]["format"]["type"] == "json_schema"
    rows = [json.loads(line) for line in path.read_text().splitlines()]
    rows = [r for r in rows if "generation_id" in r]
    assert len(rows) == 5 and all(r["actual_model"] == "fixture/returned" and
                                  r["upstream_provider"] == "Fixture" and r["cost_usd"] == 0 and
                                  r["cache_write_tokens"] == 0 and r["total_tokens"] == 17 for r in rows)


async def test_required_search_local_continuation(wire):
    posts, gets, outputs = wire
    reasoning = {"type": "reasoning", "id": "rs", "encrypted_content": "opaque", "summary": []}
    call = {"type": "function_call", "id": "fc", "call_id": "call", "name": "echo", "arguments": "{}"}
    outputs.append([reasoning, call])
    registry = LocalToolRegistry()
    registry.register(LocalToolSpec("echo", "Echo", {"type": "object", "properties": {}}), lambda _: {})
    client = OpenRouterClient("fixture")
    try:
        async def send(request):
            return await client.responses.create(**request)
        await LocalToolExecutor(registry).run(send, {
            "model": MODEL, "input": "hello", "tools": [{"type": "web_search"}, *registry.schemas()],
            "tool_choice": "required",
        }, provider="openrouter")
    finally:
        await client.close()
    assert len(posts) == 2 and len(gets) == 1
    assert posts[0]["tool_choice"] == {"type": "allowed_tools", "mode": "required",
                                       "tools": [{"type": "openrouter:web_search"}]}
    assert posts[0]["provider"]["require_parameters"] is True
    assert posts[1]["provider"] == {"only": ["fixture"], "order": ["fixture"],
                                    "allow_fallbacks": False, "require_parameters": True}
    assert posts[1]["input"][1:3] == [reasoning, call]
    assert posts[1]["input"][3]["call_id"] == "call" and posts[1]["tool_choice"] == "auto"


@pytest.mark.parametrize(("failure", "status"), [(503, 503), ("fixture timeout", 408), (429, 429)])
async def test_no_sdk_retry_or_error_body(wire, failure, status):
    posts, gets, outputs = wire
    outputs.append(failure)
    client = OpenRouterClient("fixture")
    try:
        with pytest.raises(ProviderAPIError) as error:
            await client.responses.create(model=MODEL, input="fixture")
    finally:
        await client.close()
    assert error.value.status_code == status and "private" not in str(error.value)
    assert len(posts) == 1 and not gets


def test_endpoint_support_distinguishes_unknown():
    available = {"endpoints": [{"tag": "a", "supported_parameters": ["tools"]},
                               {"tag": "b", "supported_parameters": []}]}
    assert parameter_support(available, "tools", ["a"]) is True
    assert parameter_support(available, "tools", ["b"]) is False
    assert parameter_support(available, "tools") is None
    assert parameter_support({}, "tools") is None

async def test_media_wire_preserves_reference_labels(wire):
    posts, gets, _ = wire
    client = OpenRouterClient("fixture")
    bot = LLM(Settings(discord_token="fixture", provider="openrouter", model=MODEL,
                       usage_log_path="", chat_web_search=False), client=client)
    store = Store.create(":memory:")
    image = VisualInput(b"fixture", "image/png", "attachment", message_id="image-fixture")
    audio = AudioInput(b"fixture", "audio/wav", "attachment", "reply", "explicit_reply",
                       "audio-fixture", "fixture-author", "test", "reference text", "", "sample.wav", 1)
    visual_token = CURRENT_VISUAL_INPUTS.set((image,))
    audio_token = CURRENT_AUDIO_INPUTS.set((audio,))
    try:
        await bot.answer(store, Scope(None, 1, 2), "test", "hello")
    finally:
        CURRENT_AUDIO_INPUTS.reset(audio_token)
        CURRENT_VISUAL_INPUTS.reset(visual_token)
        store.close()
        await bot.close()
    assert len(posts) == len(gets) == 1
    blocks = [block for msg in posts[0]["input"] if isinstance(msg.get("content"), list)
              for block in msg["content"]]
    assert {"type": "input_audio", "input_audio": {"data": "Zml4dHVyZQ==", "format": "wav"}} in blocks
    assert {"type": "input_image", "image_url": "data:image/png;base64,Zml4dHVyZQ==", "detail": "auto"} in blocks
    assert any("audio-fixture" in block.get("text", "") and "explicit_reply" in block["text"] for block in blocks)


async def test_unknown_media_and_unsupported_effort_stop_before_generation(wire):
    posts, _, _ = wire
    client = OpenRouterClient("fixture")
    try:
        with pytest.raises(ValueError, match="unknown"):
            await client.responses.create(model="fixture-alias", input=[{"role": "user", "content": [
                {"type": "input_image", "image_url": "data:image/png;base64,AA==", "detail": "auto"}]}])
        with pytest.raises(ValueError, match="reasoning effort"):
            await client.responses.create(model=MODEL, input="test", reasoning={"effort": "max"})
    finally:
        await client.close()
    assert not posts

@pytest.mark.parametrize("model", ["openrouter/auto", "openrouter/free", "openrouter/pareto", "@preset/test"])
async def test_automatic_router_rejected(wire, model):
    posts, _, _ = wire
    client = OpenRouterClient("fixture")
    try:
        with pytest.raises(ValueError, match="automatic model routing"):
            await client.responses.create(model=model, input="fixture")
    finally:
        await client.close()
    assert not posts


def test_catalog_variant_keys():
    assert catalog_model_slug("fixture/model:free") == "fixture/model:free"
    assert catalog_model_slug("fixture/model:nitro") == "fixture/model"
    assert catalog_model_slug("fixture/model:floor") == "fixture/model"
    assert catalog_model_slug("fixture/model:exacto") == "fixture/model"
    assert catalog_model_slug("fixture-alias") is None


async def test_required_parameters_override_false(wire):
    posts, _, _ = wire
    settings = Settings(discord_token="fixture", provider="openrouter",
                        openrouter_provider_preferences={"require_parameters": False})
    client = OpenRouterClient("fixture", settings=settings)
    try:
        await client.responses.create(model=MODEL, input="fixture", reasoning={"effort": "low"})
    finally:
        await client.close()
    assert posts[0]["provider"]["require_parameters"] is True


async def test_unknown_answer_media_preserves_text(wire):
    posts, _, _ = wire
    client = OpenRouterClient("fixture")
    bot = LLM(Settings(discord_token="fixture", provider="openrouter", model="fixture-alias",
                       usage_log_path="", chat_web_search=False), client=client)
    store = Store.create(":memory:")
    image = VisualInput(b"fixture", "image/png", "attachment", message_id="image-fixture")
    token = CURRENT_VISUAL_INPUTS.set((image,))
    try:
        await bot.answer(store, Scope(None, 1, 2), "test", "hello")
    finally:
        CURRENT_VISUAL_INPUTS.reset(token)
        store.close()
        await bot.close()
    assert len(posts) == 1
    assert "could not be read" in posts[0]["instructions"]
    assert "input_image" not in json.dumps(posts[0]["input"])


async def test_ambiguous_continuation_stops_before_generation(wire):
    posts, _, _ = wire
    client = OpenRouterClient("fixture")
    try:
        with pytest.raises(ValueError, match="unknown or ambiguous"):
            await client.responses.create(model=MODEL, input="fixture",
                                          _hina_openrouter_selected_provider="Different host")
    finally:
        await client.close()
    assert not posts

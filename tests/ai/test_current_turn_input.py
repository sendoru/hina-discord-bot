"""Textless Discord turns retain a native, attributed boundary across transports."""
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest

from hina_bot.ai import providers
from hina_bot.ai.context_input import EMPTY_CURRENT_TURN_POLICY, EMPTY_CURRENT_TURN_PREFIX, build_context_input
from hina_bot.ai.pdf_transport import GeminiInlinePDF, prepare_answer_references, text_reference_observation
from hina_bot.ai.vision import VisualInput, _augment_input, _with_hina_visual_reference

MODES = ["legacy", "structured", "structured_with_replay"]
SCENARIOS = ["call", "reply_text", "reply_embed", "attachment", "reply_gif", "normal"]


def make_input(mode, provider, scenario):
    reply = scenario in {"reply_text", "reply_embed", "reply_gif"}
    current = scenario == "attachment"
    context = {
        "current_speaker": {"user_id": "100"},
        "current_interaction": {
            "trigger_kind": "explicit", "reply_target": {"user_id": "200"} if reply else None,
        },
        "active_reply_chain": [
            {"context_kind": "replied_message", "author_user_id": "200", "role": "user",
             "content": "reply body"},
        ] if reply else [],
        "server_note": "background reference",
    }
    content = "actual user text" if scenario == "normal" else ""
    messages = build_context_input(
        context, content, mode=mode, provider=provider, current_media=("image",) if current else (),
    )
    visual = None
    if current:
        visual = VisualInput(b"current-image", "image/png", "attachment")
    elif scenario in {"reply_embed", "reply_gif"}:
        visual = VisualInput(
            b"reply-image", "image/png", "embed" if scenario == "reply_embed" else "attachment",
            "preview" if scenario == "reply_embed" else "decoded.gif",
            context_kind="replied_message", reference_strength="explicit_reply",
            message_id="123", author_user_id="200",
        )
    return messages, visual


def add_media(messages, visual):
    if visual is None:
        return messages
    return _with_hina_visual_reference(
        _augment_input(messages, (visual,)),
        ("data:image/png;base64,cmVm",), include_media_resolution=False,
    )


def marker(texts):
    matches = [text for text in texts if text.startswith(EMPTY_CURRENT_TURN_PREFIX)]
    assert len(matches) == 1
    return json.loads(matches[0].split("\n", 1)[1])


def assert_turn(texts, scenario):
    if scenario == "normal":
        assert "actual user text" in texts
        assert not any(t.startswith(EMPTY_CURRENT_TURN_PREFIX) for t in texts)
        return
    current = marker(texts)
    assert current["content"] == ""
    assert current["trigger_kind"] == "explicit"
    assert current["has_reply_reference"] == scenario.startswith("reply_")
    assert current["current_media"] == (["image"] if scenario == "attachment" else [])


def native_texts(messages):
    return [
        part if isinstance(part, str) else part["text"]
        for message in messages
        for part in (
            message["content"] if isinstance(message["content"], list) else [message["content"]]
        )
        if isinstance(part, str) or part.get("type") == "input_text"
    ]


@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize("provider", ["gemini", "openai", "openrouter"])
@pytest.mark.parametrize("scenario", SCENARIOS)
def test_common_input_preserves_textless_turn_and_media_sources(mode, provider, scenario):
    messages, visual = make_input(mode, provider, scenario)
    messages = add_media(messages, visual)
    texts = native_texts(messages)
    assert_turn(texts, scenario)
    if visual:
        # The official reference and historical/current attachment remain separate parts.
        images = [part for row in messages if isinstance(row["content"], list)
                  for part in row["content"] if part.get("type") == "input_image"]
        assert len(images) == 2
        assert images[0]["image_url"] == "data:image/png;base64,cmVm"
        assert images[1]["image_url"] == visual.input_url()


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize("api", ["interactions", "generate_content"])
@pytest.mark.parametrize("transport", ["text", "pdf", "fallback"])
async def test_gemini_textless_embed_wire(monkeypatch, mode, api, transport):
    messages, visual = make_input(mode, "gemini", "reply_embed")
    async def encode(source):
        obs = text_reference_observation(source, "")
        if transport == "fallback":
            obs["fallback"] = "encode_deadline"
            return None, obs
        doc = GeminiInlinePDF(source.encode(), 1)
        obs.update(transport="pdf", pages=1, pdf_bytes=len(doc.pdf))
        return doc, obs

    if transport != "text":
        encoder = AsyncMock(side_effect=encode)
        messages, has_pdf = await prepare_answer_references(messages, encoder=encoder)
        assert has_pdf == (transport == "pdf")
        assert all(EMPTY_CURRENT_TURN_PREFIX not in call.args[0] for call in encoder.await_args_list)
    messages = add_media(messages, visual)
    bodies = []
    def capture(request):
        bodies.append(json.loads(request.content))
        return httpx.Response(200, json={"status": "completed", "steps": [],
            "candidates": [{"finishReason": "STOP", "content": {"parts": [{"text": "ok"}]}}]})

    monkeypatch.setattr(providers, "encode_reference", AsyncMock(side_effect=encode))
    async with httpx.AsyncClient(transport=httpx.MockTransport(capture)) as http:
        adapter = providers._GeminiResponses(http, settings=SimpleNamespace(
            gemini_pdf_transport="pdf" if transport != "text" else "text",
        ))
        await adapter.create(model="fixture", input=messages, instructions=EMPTY_CURRENT_TURN_POLICY,
                             _hina_gemini_api=api)
    body = bodies[0]
    if api == "interactions":
        texts = [part["text"] for row in body["input"] for part in row["content"]
                 if part["type"] == "text"]
    else:
        texts = [part["text"] for row in body["contents"] for part in row["parts"] if "text" in part]
    assert_turn(texts, "reply_embed")
    assert ("application/pdf" in json.dumps(body)) == (transport == "pdf")


@pytest.mark.asyncio
@pytest.mark.parametrize("provider", ["openai", "openrouter"])
@pytest.mark.parametrize("mode", MODES)
async def test_responses_wire_preserves_textless_embed(provider, mode):
    messages, visual = make_input(mode, provider, "reply_embed")
    messages = add_media(messages, visual)
    bodies = []
    def capture(request):
        bodies.append(json.loads(request.content))
        return httpx.Response(200, json={"id": "fixture", "object": "response", "created_at": 1,
            "model": "fixture/model", "status": "completed", "output": []})

    async with httpx.AsyncClient(transport=httpx.MockTransport(capture)) as http:
        client = providers.OpenAIClient(api_key="fixture", http_client=http)
        responses = client.responses
        if provider == "openrouter":
            responses = providers._OpenRouterResponses(responses)
        try:
            await responses.create(model="fixture/model", input=messages,
                                   instructions=EMPTY_CURRENT_TURN_POLICY)
        finally:
            await client.close()
    assert_turn(native_texts(bodies[0]["input"]), "reply_embed")

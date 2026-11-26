import base64
import json
from dataclasses import dataclass
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock

import httpx
import pytest

from hina_bot.ai import pdf_transport, providers
from hina_bot.ai.information_pipeline import InformationPipeline
from hina_bot.ai.pdf_transport import GeminiInlinePDF, PreparedPDFReference
from hina_bot.ai.prompts import load_prompt
from hina_bot.ai.reference_context_blocks import PDFReferenceText
from hina_bot.core.config import Settings
from hina_bot.core.routing import Scope
from hina_bot.core.store import Store


@dataclass(frozen=True, kw_only=True)
class PDFSettings(Settings):
    gemini_pdf_transport: str = "pdf"
    gemini_pdf_scope: str = "all"


def mock_encoder(monkeypatch, success):
    async def encode(source):
        document = GeminiInlinePDF(source.encode(), 1) if success else None
        observation = pdf_transport.text_reference_observation(
            source, "" if success else "unsupported_glyph",
        )
        if success:
            observation.update(transport="pdf", pages=1, pdf_bytes=len(document.pdf))
        return document, observation

    encoder = AsyncMock(side_effect=encode)
    monkeypatch.setattr(pdf_transport, "encode_reference", encoder)
    # A prepared reference must not be re-encoded in the provider.
    monkeypatch.setattr(providers, "encode_reference", AsyncMock(
        side_effect=AssertionError("unexpected second encoding"),
    ))
    return encoder


@pytest.mark.asyncio
@pytest.mark.parametrize(("mode", "success", "expected"), [
    ("auto", True, "delegated"),
    ("auto", False, "native"),
    ("native", True, "native"),
    ("delegated", True, "delegated"),
    ("delegated", False, "delegated"),
])
@pytest.mark.parametrize("api", ["interactions", "generate_content"])
async def test_pdf_answer_prepares_before_tools_and_preserves_wire_compatibility(
    tmp_path, monkeypatch, mode, success, expected, api,
):
    encoder = mock_encoder(monkeypatch, success)
    config = PDFSettings(
        discord_token="test", provider="gemini", gemini_api_key="test",
        model="gemini-test", fast_model="gemini-test", smart_model="gemini-test",
        code_execution_backend=mode, db_path=str(tmp_path / "admin.sqlite3"),
        usage_log_path="", chat_web_search=True,
        gemini_search_api_mode=api,
    )
    bodies = []
    def capture(request):
        body = json.loads(request.content)
        bodies.append(body)
        if api == "generate_content":
            return httpx.Response(200, json={"candidates": [{
                "finishReason": "STOP", "content": {"parts": [{"text": "answer"}]},
            }]})
        return httpx.Response(200, json={"status": "completed", "steps": [{
            "type": "model_output", "content": [{"type": "text", "text": "answer"}],
        }]})

    async with httpx.AsyncClient(transport=httpx.MockTransport(capture)) as http:
        adapter = providers._GeminiResponses(http, settings=config)
        fake = NS(provider_name="gemini", responses=adapter, close=AsyncMock())
        llm = InformationPipeline(config, client=fake)
        store = Store.create(":memory:")
        try:
            reply = await llm.answer(
                store, Scope(None, 10, 100), "user", "hello", channel_context=[],
            )
            assert reply == "answer"
        finally:
            await llm.close()
            store.close()
    assert len(bodies) == 1
    body = bodies[0]
    serialized = json.dumps(body)
    assert serialized.count("application/pdf") == int(success and mode != "native")
    assert ("app_guidance" in serialized) == (success and mode != "native")
    system = body["system_instruction"]
    if isinstance(system, dict):
        system = system["parts"][0]["text"]
    moved = success and mode != "native"
    expected_guidance = {
        "dialogue_context": load_prompt("dialogue_context.md"),
        "turn_response": load_prompt("turn_response.md"),
        "conversation_space": load_prompt("conversation_dm.md"),
    }
    for guidance in expected_guidance.values():
        assert (guidance in system) == (not moved)
    for name in ("base.md", "final_output.md"):
        assert load_prompt(name).strip() in system
    if moved:
        assert pdf_transport.DOCUMENT_GUIDANCE in system
    if moved:
        def documents(value):
            if isinstance(value, dict):
                if value.get("mime_type", value.get("mimeType")) == "application/pdf":
                    yield base64.b64decode(value["data"]).decode()
                for item in value.values():
                    yield from documents(item)
            elif isinstance(value, list):
                for item in value:
                    yield from documents(item)

        (source,) = documents(body)
        guidance_map = {}
        for line in source.splitlines():
            if line.startswith('{"app_guidance":'):
                guidance_map.update(json.loads(line)["app_guidance"])
        for label, guidance in expected_guidance.items():
            assert guidance_map[label] == guidance
        assert load_prompt("final_output.md") not in guidance_map.values()

    assert ("request_computation" in serialized) == (expected == "delegated")
    if api == "generate_content":
        native = any("code_execution" in tool for tool in body.get("tools", []))
    else:
        native = any(tool.get("type") == "code_execution" for tool in body.get("tools", []))
    assert native == (expected == "native")
    assert (encoder.await_count > 0) == (mode != "native")


@pytest.mark.asyncio
async def test_prepared_references_preserve_original_and_reuse_result(monkeypatch):
    encoder = mock_encoder(monkeypatch, True)
    part = PDFReferenceText(type="input_text", text="record123")
    messages = [{"role": "user", "content": [part]}]
    projected, has_pdf = await pdf_transport.prepare_answer_references(messages)
    assert has_pdf
    assert messages[0]["content"][0] is part
    prepared = projected[0]["content"][0]
    assert isinstance(prepared, PreparedPDFReference)
    assert json.dumps(projected) == json.dumps(messages)
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: None)) as http:
        adapter = providers._GeminiResponses(http, settings=NS(gemini_pdf_transport="pdf"))
        for _ in range(2):
            payload = {"input": providers._gemini_input(projected)}
            metrics = await adapter._reference_transport(
                payload, {"input": projected}, "input", providers._gemini_input,
            )
            assert payload["input"][0]["content"][0]["type"] == "document"
            assert metrics[0]["transport"] == "pdf"
    encoder.assert_awaited_once()

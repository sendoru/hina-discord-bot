import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest

from hina_bot.ai import pdf_transport, providers
from hina_bot.ai.pdf_transport import (
    GeminiInlinePDF,
    InstructionReferenceText,
    consolidate_references,
    prepare_answer_references,
    text_reference_observation,
    transport_coverage,
)
from hina_bot.ai.reference_context_blocks import PDFReferenceText, reference_context_blocks


def fixture_encoder(source):
    obs = text_reference_observation(source, "")
    if "bad_record" in source:
        obs["fallback"] = "unsupported_glyph"
        return None, obs
    document = GeminiInlinePDF(source.encode(), 1)
    obs.update(transport="pdf", pages=1, pdf_bytes=len(document.pdf))
    return document, obs


@pytest.mark.asyncio
async def test_consolidated_renderer_once_and_preserves_small_sections():
    encoder = AsyncMock(side_effect=fixture_encoder)
    parts = reference_context_blocks({
        "current_speaker": {"id": "speaker"},
        "active_reply_chain": [{"id": "reply"}],
        "channel_recent_messages": [{"id": "message"}],
        "lore_reference": [],
        "available_custom_emojis": [],
    })
    messages = [{"role": "user", "content": parts}, {"role": "user", "content": "actual current"}]
    projected, has_pdf = await prepare_answer_references(
        messages, encoder=encoder,
        instruction_parts=[InstructionReferenceText("character", "app persona")],
    )
    assert has_pdf
    encoder.assert_awaited_once()
    prepared = [part for part in projected[0]["content"] if isinstance(part, pdf_transport.PreparedPDFReference)]
    physical = [doc for part in prepared for _, doc, _ in part.segments if doc is not None]
    assert len(physical) == 1
    source = physical[0].pdf.decode()
    assert json.loads(source.splitlines()[0]) == {"app_guidance": {"character": "app persona"}}
    assert all(marker in source for marker in ("reply", "message", "lore_reference", "available_custom_emojis"))
    assert "speaker" not in source and "actual current" not in source
    coverage = transport_coverage([obs for part in prepared for _, _, obs in part.segments])
    assert coverage["documents"] == 1
    assert coverage["pages"] == 1
    assert coverage["eligible_coverage"] == 1.0
    assert messages[0]["content"] is parts


@pytest.mark.asyncio
async def test_partial_consolidation_transmits_only_one_document():
    parts = [
        PDFReferenceText(type="input_text", text="good1"),
        PDFReferenceText(type="input_text", text="bad_record"),
        PDFReferenceText(type="input_text", text="good2"),
    ]
    prepared = await consolidate_references(parts, encoder=AsyncMock(side_effect=fixture_encoder))
    wire = [(fragment, doc, obs) for part in prepared for fragment, doc, obs in part.segments]
    documents = [doc for _, doc, _ in wire if doc]
    assert len(documents) == 1
    assert documents[0].pdf == b"good1\ngood2"
    assert [fragment["text"] for fragment, _, _ in wire if fragment is not None] == ["bad_record"]
    assert transport_coverage([obs for _, _, obs in wire])["documents"] == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("api", ["interactions", "generate_content"])
@pytest.mark.parametrize("fallback", ["none", "size", "boundary"])
async def test_app_instruction_moves_only_when_document_is_transmitted(monkeypatch, api, fallback):
    persona = "app persona description"
    instructions = "native policy\n" + (persona if fallback != "boundary" else "different persona") + "\ncurrent time"
    doc = b"X" * 8_000_000 if fallback == "size" else b"pdf"
    async def encode(source):
        observation = text_reference_observation(source, "")
        observation.update(transport="pdf", pages=1, pdf_bytes=len(doc))
        return GeminiInlinePDF(doc, 1), observation

    original = [
        {"role": "user", "content": reference_context_blocks({"conversation_history": [{"content": "old"}]})},
        {"role": "user", "content": "actual current"},
    ]
    projected, has_pdf = await prepare_answer_references(
        original, instruction_parts=[InstructionReferenceText("character", persona)], encoder=encode,
    )
    assert has_pdf
    monkeypatch.setattr(providers, "encode_reference", AsyncMock(side_effect=AssertionError("re-encoded")))
    bodies = []

    def capture(request):
        bodies.append(json.loads(request.content))
        return httpx.Response(200, json={"status": "completed", "steps": [],
                                         "candidates": [{"finishReason": "STOP", "content": {"parts": [{"text": "ok"}]}}]})

    async with httpx.AsyncClient(transport=httpx.MockTransport(capture)) as http:
        adapter = providers._GeminiResponses(http, settings=SimpleNamespace(gemini_pdf_transport="pdf"))
        result = await adapter.create(model="fixture", input=projected, instructions=instructions,
                                      _hina_gemini_api=api)
    body = bodies[0]
    system = body["system_instruction"]
    if isinstance(system, dict):
        system = system["parts"][0]["text"]
    assert ("application/pdf" in json.dumps(body)) == (fallback == "none")
    if fallback == "none":
        assert persona not in system and "app_guidance" in system
        assert "native policy" in system and "current time" in system
    else:
        assert system == instructions
        assert "old" in json.dumps(body)
    assert "actual current" in json.dumps(body)
    coverage = transport_coverage(result._hina_reference_transport)
    assert coverage["documents"] == int(fallback == "none")
    assert coverage["pages"] == int(fallback == "none")


def test_provider_modality_tokens_do_not_invent_unobserved_document_tokens():
    output = providers._gemini_generate_content_output({
        "usageMetadata": {"promptTokensDetails": [
            {"modality": "TEXT", "tokenCount": 100}, {"modality": "IMAGE", "tokenCount": 200},
        ]},
    })
    assert output._hina_modality_tokens == {"input": {"text": 100, "image": 200}}
    assert providers._gemini_output({"usage": {}})._hina_modality_tokens == {}

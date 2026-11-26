import asyncio
import json
from threading import Event
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
from hina_bot.ai.prompts import load_prompt
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


@pytest.mark.asyncio
@pytest.mark.parametrize("success", [True, False])
async def test_whole_memory_and_vision_resources_preserve_native_boundaries(success):
    memory = load_prompt("structured_memory.md")
    vision = load_prompt("vision_input.md").strip()
    output = load_prompt("final_output.md")
    instructions = f"native policy\n{memory}\n{output}\n{vision}"
    parts = [
        InstructionReferenceText("structured_memory", memory),
        InstructionReferenceText("vision_input", vision),
    ]
    messages = [{"role": "user", "content": [
        PDFReferenceText(type="input_text", text="reference"),
    ]}]
    encoder = AsyncMock(side_effect=fixture_encoder if success else lambda source: (
        None, text_reference_observation(source, "unsupported_glyph"),
    ))
    projected, has_pdf = await prepare_answer_references(
        messages, instruction_parts=parts, encoder=encoder,
    )
    assert has_pdf is success
    payload = {"input": providers._gemini_input(projected), "system_instruction": instructions}
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: None)) as http:
        adapter = providers._GeminiResponses(http, settings=SimpleNamespace(gemini_pdf_transport="pdf"))
        metrics = await adapter._reference_transport(
            payload, {"input": projected}, "input", providers._gemini_input,
        )
    system = payload["system_instruction"]
    if success:
        assert memory not in system and vision not in system
        assert output in system and pdf_transport.DOCUMENT_GUIDANCE in system
        assert "audience=owner" in system and "sensitive" in system
        assert transport_coverage(metrics)["documents"] == 1
    else:
        assert system == instructions
        assert transport_coverage(metrics)["documents"] == 0


@pytest.mark.asyncio
async def test_deadline_keeps_largest_verified_fragment_and_original_instruction():
    parts = [
        InstructionReferenceText("persona", "good persona"),
        PDFReferenceText(type="input_text", text="bad_record"),
        PDFReferenceText(type="input_text", text="good longer reference"),
    ]

    async def encode(source):
        if "good persona" in source and "good longer reference" in source and "bad_record" not in source:
            await asyncio.sleep(60)
        return fixture_encoder(source)

    prepared = await consolidate_references(parts, encoder=encode, deadline_seconds=0.1)
    wire = [(fragment, doc, obs) for part in prepared for fragment, doc, obs in part.segments]
    (document,) = [doc for _, doc, _ in wire if doc is not None]
    assert document.pdf == b"good longer reference"
    assert prepared[0].original is parts[0]
    assert prepared[0].document is None
    assert wire[0][0] is parts[0]
    assert wire[0][2]["fallback"] == "encode_deadline"
    assert wire[1][0] is parts[1]
    assert transport_coverage([obs for _, _, obs in wire])["documents"] == 1


@pytest.mark.asyncio
async def test_cancelled_encoder_waiter_does_not_start_render(monkeypatch):
    entered = Event()
    release = Event()
    calls = []

    def render(source, *, cancelled):
        calls.append(source)
        entered.set()
        release.wait(timeout=2)
        return fixture_encoder(source)[0], ""

    monkeypatch.setattr(pdf_transport, "_encode", render)
    owner = asyncio.create_task(pdf_transport.encode_reference("owner"))
    try:
        assert await asyncio.to_thread(entered.wait, 1)
        waiter = asyncio.create_task(pdf_transport.encode_reference("cancelled waiter"))
        await asyncio.sleep(0.03)
        waiter.cancel()
        with pytest.raises(asyncio.CancelledError):
            await waiter
    finally:
        release.set()
        await owner
    next_doc, _ = await pdf_transport.encode_reference("next")
    assert next_doc is not None
    assert calls == ["owner", "next"]


@pytest.mark.asyncio
async def test_cancelled_active_renderer_releases_lock_cooperatively(monkeypatch):
    entered = Event()
    exited = Event()

    def render(source, *, cancelled):
        if source == "slow":
            entered.set()
            cancelled.wait(timeout=2)
            exited.set()
            return None, "encode_deadline"
        return fixture_encoder(source)[0], ""

    monkeypatch.setattr(pdf_transport, "_encode", render)
    owner = asyncio.create_task(pdf_transport.encode_reference("slow"))
    try:
        assert await asyncio.to_thread(entered.wait, 1)
    finally:
        owner.cancel()
        with pytest.raises(asyncio.CancelledError):
            await owner
    assert await asyncio.to_thread(exited.wait, 1)
    document, observation = await pdf_transport.encode_reference("next")
    assert document is not None and observation["transport"] == "pdf"


@pytest.mark.asyncio
async def test_large_synthetic_answer_uses_single_real_pdf():
    # ASCII fixture avoids requiring a specific OS font; no remote model calls.
    source = ("Static lore record: an ordinary reference with stable identifiers. " * 800).strip()
    part = InstructionReferenceText("static_lore", source, guidance=False)
    reference = PDFReferenceText(type="input_text", text="conversation context")
    prepared = await consolidate_references([part, reference])
    documents = [doc for item in prepared for _, doc, _ in item.segments if doc]
    assert len(documents) == 1
    assert documents[0].pdf.startswith(b"%PDF")
    assert transport_coverage([obs for item in prepared for _, _, obs in item.segments])["eligible_coverage"] == 1.0

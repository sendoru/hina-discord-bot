import json

import httpx
import pytest

from hina_bot.ai import decisions
from hina_bot.ai.pdf_transport import GeminiInlinePDF, text_reference_observation


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["pdf", "failed", "size", "timeout"])
async def test_decision_pdf_preserves_question_and_candidate_ids_and_sends_once(monkeypatch, mode):
    encoded = []
    sent = []

    async def encode(source):
        if mode == "timeout":
            import asyncio

            await asyncio.sleep(5)
        encoded.extend(json.loads(line) for line in source.splitlines())
        if mode == "failed":
            return None, text_reference_observation(source, "unsupported_glyph")
        pdf = GeminiInlinePDF(b"X" * decisions.MAX_REQUEST_BYTES if mode == "size" else b"PDF", 1)
        obs = text_reference_observation(source, "")
        obs.update(transport="pdf", pages=1, pdf_bytes=len(pdf.pdf))
        return pdf, obs

    monkeypatch.setattr(decisions, "encode_reference", encode)
    if mode == "timeout":
        monkeypatch.setattr(decisions, "PDF_PREP_TIMEOUT", 0.01)

    def capture(request):
        body = json.loads(request.content)
        sent.append(body)
        assert list(body["questions"]) == ["c1", "c2"]
        if mode == "pdf":
            control = json.loads(body["input"][0]["text"].splitlines()[0])
            assert control["turn"] == {"message": "now"}
            assert "selection_rules" in control
            assert body["input"][1]["type"] == "document"
            assert body["input"][1]["mime_type"] == "application/pdf"
        else:
            shared = json.loads(body["input"])
            assert shared["turn"] == {"message": "now"}
            assert shared["candidates"] == {"c1": {"id": "A"}, "c2": {"id": "B"}}
        return httpx.Response(200, json={
            "answers": {
                "c2": {"type": "noul", "noul": 0.9},
                "c1": {"type": "noul", "noul": 0.1},
            },
            "usage": {"total_input_tokens": 42},
        })

    async with httpx.AsyncClient(transport=httpx.MockTransport(capture)) as http:
        result = await decisions.DecisionsClient(http).evaluate(
            api_key="fixture", domain="memory", query={"message": "now"},
            candidates={"original.a": {"id": "A"}, "original.b": {"id": "B"}},
            pdf_transport=True,
        )
    assert len(sent) == 1
    if mode != "timeout":
        assert encoded == [{"candidates": {"c1": {"id": "A"}, "c2": {"id": "B"}}}] or mode == "failed"
    assert result.scores == {"original.a": 0.1, "original.b": 0.9}
    assert result.metrics()["decision_transport"]["transport"] == ("pdf" if mode == "pdf" else "text")
    assert result.input_tokens == 42
    if mode == "timeout":
        assert result.transport["fallback"] == "encode_deadline"


@pytest.mark.asyncio
async def test_pdf_http_error_does_not_retry(monkeypatch):
    async def encode(source):
        obs = text_reference_observation(source, "")
        obs.update(transport="pdf", pages=1, pdf_bytes=3)
        return GeminiInlinePDF(b"PDF", 1), obs

    monkeypatch.setattr(decisions, "encode_reference", encode)
    calls = []

    def capture(request):
        calls.append(json.loads(request.content))
        return httpx.Response(400, json={"error": "invalid"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(capture)) as http:
        result = await decisions.DecisionsClient(http).evaluate(
            api_key="fixture", domain="lore", query={},
            candidates={"id": {"text": "candidate"}}, pdf_transport=True,
        )
    assert len(calls) == 1
    assert result.reason == "http_error"
    assert result.scores is None
    assert result.metrics()["decision_transport"]["transport"] == "pdf"


@pytest.mark.asyncio
async def test_partial_candidates_keep_one_document_and_exact_candidate_records(monkeypatch):
    import base64

    sent = []

    async def encode(source):
        observation = text_reference_observation(source, "")
        if "unrenderable" in source:
            observation["fallback"] = "unsupported_glyph"
            return None, observation
        document = GeminiInlinePDF(source.encode(), 1)
        observation.update(transport="pdf", pages=1, pdf_bytes=len(document.pdf))
        return document, observation

    monkeypatch.setattr(decisions, "encode_reference", encode)
    blocks, observations = await decisions._candidate_pdf_input({
        "selection_rules": "rules", "turn": {"message": "now"},
        "candidates": {"c1": {"text": "one"}, "c2": {"text": "unrenderable"}, "c3": {"text": "three"}},
    })
    for block in blocks[1:]:
        source = base64.b64decode(block["data"]).decode() if block["type"] == "document" else block["text"]
        sent.extend(json.loads(line)["candidates"] for line in source.splitlines())
    merged = {}
    for batch in sent:
        assert not merged.keys() & batch.keys()
        merged.update(batch)
    assert set(merged) == {"c1", "c2", "c3"}
    assert sum(block["type"] == "document" for block in blocks) == 1
    assert sum(obs.get("documents", 0) for obs in observations) == 1

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

import base64

from hina_bot.ai.providers import GeminiInlinePDF, _gemini_generate_content_contents, _gemini_input


def test_document_stays_between_reference_and_current_turn():
    pdf = b"%PDF-1.4 synthetic adapter fixture"
    messages = [{"role": "assistant", "content": "previous"},
                {"role": "user", "content": ["before", GeminiInlinePDF(pdf), "after"]}]
    steps = _gemini_input(messages)
    assert [step["type"] for step in steps] == ["model_output", "user_input"]
    before, document, after = steps[1]["content"]
    assert (before["text"], after["text"]) == ("before", "after")
    assert document["type"] == "document"
    assert document["mime_type"] == "application/pdf"
    assert base64.b64decode(document["data"]) == pdf
    turns = _gemini_generate_content_contents(messages)
    assert [turn["role"] for turn in turns] == ["model", "user"]
    before, document, after = turns[1]["parts"]
    assert (before["text"], after["text"]) == ("before", "after")
    assert document["inline_data"]["mime_type"] == "application/pdf"
    assert base64.b64decode(document["inline_data"]["data"]) == pdf


def test_direct_reply_current_speaker_and_role_replay_remain_native():
    from hina_bot.ai.reference_context_blocks import PDFReferenceText, reference_context_blocks

    blocks = reference_context_blocks({
        "current_speaker": {"id": "speaker123"},
        "active_reply_chain": [{"role": "user", "content": "reply456"}],
        "lore_reference": ["lore789"],
    })
    assert [isinstance(block, PDFReferenceText) for block in blocks] == [False, False, True]
    assert all(block["type"] == "input_text" for block in blocks)


async def test_pdf_decisions_send_once_with_native_control_and_exact_candidate_map():
    import io
    import json

    import httpx
    from pypdf import PdfReader

    from hina_bot.ai.decisions import DecisionsClient

    sent = []
    def capture(request):
        body = json.loads(request.content)
        sent.append(len(request.content))
        control, document = body["input"]
        assert json.loads(control["text"])["turn"] == {"message": "now"}
        pdf = PdfReader(io.BytesIO(base64.b64decode(document["data"])))
        assert json.loads(pdf.pages[0].extract_text()) == {"candidates": {"c1": {"id": "123"}}}
        assert list(body["questions"]) == ["c1"]
        return httpx.Response(200, json={"answers": {"c1": {"type": "noul", "noul": 0.8}}})

    async with httpx.AsyncClient(transport=httpx.MockTransport(capture)) as http:
        result = await DecisionsClient(http).evaluate(
            api_key="synthetic", domain="lore", query={"message": "now"},
            candidates={"original": {"id": "123"}}, pdf_transport=True,
        )
    assert result.scores == {"original": 0.8}
    assert sent == [result.request_bytes]
    assert result.transport_metrics["transport"] == "pdf"


async def test_reference_transport_requires_local_marker_and_preserves_original():
    from types import SimpleNamespace

    import httpx

    from hina_bot.ai.providers import _GeminiResponses
    from hina_bot.ai.reference_context_blocks import PDFReferenceText

    reference = PDFReferenceText(type="input_text", text="abc123")
    messages = [{"role": "user", "content": [reference, dict(reference)]}]
    kwargs = {"input": messages}
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: None)) as http:
        settings = SimpleNamespace(gemini_pdf_transport="pdf")
        adapter = _GeminiResponses(http, settings=settings)
        for mode, stored, expected in [("pdf", False, True), ("auto", False, False),
                                       ("text", False, False), ("pdf", True, False)]:
            settings.gemini_pdf_transport = mode
            payload = {"input": _gemini_input(messages), "store": stored}
            metrics = await adapter._reference_transport(payload, kwargs, "input", _gemini_input)
            first, second = payload["input"][0]["content"]
            assert (first["type"] == "document") == expected
            assert second == {"type": "text", "text": "abc123"}
            assert bool(metrics) == expected
    assert messages[0]["content"] == [reference, dict(reference)]


async def test_multipage_ids_are_atomic_and_unrepresentable_sources_stay_text():
    import io

    from pypdf import PdfReader

    from hina_bot.ai.pdf_transport import encode_reference

    ids = [str(10**18 + i) for i in range(360)]
    source = ",".join(ids)
    document, _ = await encode_reference(source)
    again, _ = await encode_reference(source)
    assert document.pdf == again.pdf and document.pages > 1
    extracted = "\n".join(page.extract_text() for page in PdfReader(io.BytesIO(document.pdf)).pages)
    assert extracted.replace("\n", "") == source
    assert all(identifier in extracted for identifier in ids)
    for source, reason in [("x" * 200, "unbreakable_token"), ("data\x00", "unsupported_glyph")]:
        document, metrics = await encode_reference(source)
        assert document is None and metrics["fallback"] == reason

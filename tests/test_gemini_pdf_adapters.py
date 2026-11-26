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


async def test_text_decisions_send_once_with_native_control_and_exact_candidate_map():
    import json
    from types import SimpleNamespace

    import httpx

    from hina_bot.ai.decisions import DecisionsClient
    from hina_bot.ai.pdf_transport import pdf_enabled

    assert not pdf_enabled(SimpleNamespace(gemini_pdf_transport="pdf", gemini_pdf_scope="all"), "decisions")
    sent = []
    def capture(request):
        body = json.loads(request.content)
        sent.append(len(request.content))
        shared = json.loads(body["input"])
        assert shared["turn"] == {"message": "now"}
        assert shared["candidates"] == {"c1": {"id": "123"}}
        assert list(body["questions"]) == ["c1"]
        return httpx.Response(200, json={"answers": {"c1": {"type": "noul", "noul": 0.8}}})

    async with httpx.AsyncClient(transport=httpx.MockTransport(capture)) as http:
        result = await DecisionsClient(http).evaluate(
            api_key="synthetic", domain="lore", query={"message": "now"},
            candidates={"original": {"id": "123"}},
        )
    assert result.scores == {"original": 0.8}
    assert sent == [result.request_bytes]
    assert result.metrics()["decision_transport"] == {"transport": "text"}


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
                                       ("text", False, False), ("pdf", True, True)]:
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


async def test_rejected_pdf_does_not_retry_or_log_echoed_document():
    import json
    from types import SimpleNamespace

    import httpx
    import pytest

    from hina_bot.ai.providers import ProviderAPIError, _GeminiResponses
    from hina_bot.ai.reference_context_blocks import PDFReferenceText

    bodies = []
    def reject(request):
        bodies.append(json.loads(request.content))
        return httpx.Response(429, json={"error": {"message": "private-echo-of-request"}})

    async with httpx.AsyncClient(transport=httpx.MockTransport(reject)) as http:
        adapter = _GeminiResponses(http, settings=SimpleNamespace(gemini_pdf_transport="pdf"))
        with pytest.raises(ProviderAPIError) as error:
            await adapter.create(model="fixture", tools=[{"type": "web_search"}],
                                 input=[{"role": "user", "content": [
                                     PDFReferenceText(type="input_text", text="fixture123")
                                 ]}])
    assert len(bodies) == 1
    assert bodies[0]["input"][0]["content"][0]["type"] == "document"
    assert error.value.status_code == 429
    assert "private-echo" not in str(error.value)


async def test_stored_pdf_answer_preserves_wire_flag_and_continuation_id():
    import json
    from types import SimpleNamespace

    import httpx

    from hina_bot.ai.providers import _GeminiResponses
    from hina_bot.ai.reference_context_blocks import PDFReferenceText

    calls = []
    def capture(request):
        body = json.loads(request.content)
        calls.append(body)
        return httpx.Response(200, json={"id": "fixture-id", "status": "completed", "steps": []})

    async with httpx.AsyncClient(transport=httpx.MockTransport(capture)) as http:
        adapter = _GeminiResponses(http, settings=SimpleNamespace(gemini_pdf_transport="pdf"))
        response = await adapter.create(model="fixture", store=True, input=[
            {"role": "user", "content": [PDFReferenceText(type="input_text", text="sample")]},
        ])
    assert len(calls) == 1 and calls[0]["store"] is True
    assert calls[0]["input"][0]["content"][0]["type"] == "document"
    assert response._hina_interaction_id == "fixture-id"


async def test_code_execution_preserves_native_references_on_both_apis(monkeypatch):
    import json
    from types import SimpleNamespace

    import httpx

    from hina_bot.ai import providers
    from hina_bot.ai.reference_context_blocks import PDFReferenceText

    async def unexpected_encode(source):
        raise AssertionError("incompatible tools must be detected before encoding")

    monkeypatch.setattr(providers, "encode_reference", unexpected_encode)
    bodies = []
    def capture(request):
        bodies.append(json.loads(request.content))
        return httpx.Response(200, json={"steps": [], "candidates": []})

    async with httpx.AsyncClient(transport=httpx.MockTransport(capture)) as http:
        adapter = providers._GeminiResponses(http, settings=SimpleNamespace(gemini_pdf_transport="pdf"))
        for api in ("interactions", "generate_content"):
            response = await adapter.create(
                model="fixture", _hina_gemini_api=api, store=True,
                input=[{"role": "user", "content": [PDFReferenceText(type="input_text", text="record123")]}],
                tools=[{"type": "code_execution"}],
            )
            assert response._hina_reference_transport[0]["fallback"] == "code_execution_incompatible"
    first, second = bodies
    assert first["input"][0]["content"] == [{"type": "text", "text": "record123"}]
    assert second["contents"][0]["parts"] == [{"text": "record123"}]
    assert first["tools"] == [{"type": "code_execution"}]
    assert second["tools"] == [{"code_execution": {}}]
    assert first["store"] and second["store"]

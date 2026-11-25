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

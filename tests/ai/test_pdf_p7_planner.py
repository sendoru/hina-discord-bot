import json

import pytest

from hina_bot.ai.pdf_transport import (
    GeminiInlinePDF,
    plan_reference,
    text_reference_observation,
    transport_coverage,
)
from hina_bot.ai.reference_context_blocks import reference_context_blocks


@pytest.mark.asyncio
async def test_partial_reference_preserves_records_in_order_and_native_failures():
    rows = [{"id": "1"}, {"id": "bad"}, {"id": "3"}]
    part = reference_context_blocks({"channel_recent_messages": rows})[0]

    async def encoder(source):
        if '"bad"' in source:
            return None, text_reference_observation(source, "unsupported_glyph")
        obs = text_reference_observation(source, "")
        obs.update(transport="pdf", pages=1, pdf_bytes=10)
        return GeminiInlinePDF(b"fixture", 1), obs

    prepared = await plan_reference(part, encoder=encoder)
    assert prepared.document is not None
    assert dict(prepared) == dict(part)
    reconstructed = []
    observations = []
    for fragment, document, obs in prepared.segments:
        reconstructed += json.loads(fragment["text"].partition("\n")[2])["channel_recent_messages"]
        observations.append(obs)
        assert (document is None) == ('"bad"' in fragment["text"])
    assert reconstructed == rows
    coverage = transport_coverage(observations)
    assert 0 < coverage["eligible_coverage"] < 1
    assert coverage["pages"] == 2
    assert coverage["source_chars"] == len(part["text"])
    assert all(obs["wire_text_chars"] >= obs["source_chars"] for obs in observations)


@pytest.mark.asyncio
async def test_all_failed_fragments_keep_original_payload():
    part = reference_context_blocks({"conversation_history": [1, 2, 3]})[0]

    async def encoder(source):
        return None, text_reference_observation(source, "unsupported_glyph")

    prepared = await plan_reference(part, encoder=encoder)
    assert prepared.document is None
    assert prepared.segments[0][0] == part
    assert len(prepared.segments) == 1

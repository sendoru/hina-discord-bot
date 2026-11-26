"""Regression coverage for lossless PDF performance changes."""
import io
import json
from pathlib import Path

import pytest
from pypdf import PdfReader
from reportlab.pdfbase import pdfmetrics

from hina_bot.ai import pdf_transport as pdf
from hina_bot.ai.reference_context_blocks import PDFReferenceText


def text(value):
    return PDFReferenceText(type="input_text", text=value)


def candidate_records(count, *, prefix="c"):
    rows = {
        f"{prefix}{i}": {
            "id": str(10**18 + i),
            "content": "unsupported \U0010ffff" if i == 0 else "ordinary reference",
        }
        for i in range(count)
    }
    return rows, pdf.DecisionCandidateReference(
        type="input_text",
        text=json.dumps({"candidates": rows}, ensure_ascii=False, separators=(",", ":")),
    )


def fake_encoder(source):
    observation = pdf.text_reference_observation(source, "")
    if "\U0010ffff" in source:
        observation["fallback"] = "unsupported_glyph"
        return None, observation
    document = pdf.GeminiInlinePDF(source.encode(), 1)
    observation.update(transport="pdf", pages=1, pdf_bytes=len(document.pdf))
    return document, observation


def wire(prepared):
    return [entry for part in prepared for entry in part.segments]


@pytest.mark.asyncio
async def test_single_failed_part_is_not_retried():
    calls = []

    async def encode(source):
        calls.append(source)
        return fake_encoder(source)

    part = text("unsupported \U0010ffff")
    prepared = await pdf.consolidate_references([part], encoder=encode)
    assert calls == [part["text"]]
    assert prepared[0].original is part
    assert prepared[0].segments[0][0] is part
    assert prepared[0].segments[0][2]["encode_attempts"] == 1


@pytest.mark.asyncio
async def test_cached_layout_and_final_consolidation_match_uncached_real_pdf():
    good = "Repeated reference with identifiers and punctuation: 123456789."
    parts = [
        text("\n".join([good] * 30)),
        text("unsupported \U0010ffff"),
        text("\n".join([good + " other"] * 30)),
    ]

    async def uncached(source):
        return await pdf.encode_reference(source)

    cached = wire(await pdf.consolidate_references(parts))
    ordinary = wire(await pdf.consolidate_references(parts, encoder=uncached))
    (cached_doc,) = [doc for _, doc, _ in cached if doc is not None]
    (ordinary_doc,) = [doc for _, doc, _ in ordinary if doc is not None]
    assert cached_doc.pdf == ordinary_doc.pdf
    assert cached_doc.pages == ordinary_doc.pages
    assert [fragment for fragment, _, _ in cached if fragment is not None] == [parts[1]]
    assert pdf.transport_coverage([obs for _, _, obs in cached])["documents"] == 1


@pytest.mark.asyncio
async def test_fragment_budget_reserves_good_siblings_and_preserves_all_candidate_ids():
    rows, part = candidate_records(1000)
    calls = []

    async def encode(source):
        calls.append(source)
        return fake_encoder(source)

    prepared = await pdf.consolidate_references([part], encoder=encode)
    segments = wire(prepared)
    (document,) = [doc for _, doc, _ in segments if doc is not None]
    recovered = {}
    for source in [
        document.pdf.decode(),
        *[fragment["text"] for fragment, _, _ in segments if fragment is not None],
    ]:
        for line in source.splitlines():
            group = json.loads(line)["candidates"]
            assert not recovered.keys() & group.keys()
            recovered.update(group)
    native_ids = {
        key
        for fragment, _, _ in segments if fragment is not None
        for key in json.loads(fragment["text"])["candidates"]
    }
    assert recovered == rows
    assert "c0" in native_ids
    assert "c999" not in native_ids
    assert len(native_ids) <= 16
    assert len(calls) <= pdf.MAX_CONSOLIDATED_ATTEMPTS


@pytest.mark.asyncio
async def test_global_budget_reserves_later_sections(monkeypatch):
    monkeypatch.setattr(pdf, "MAX_CONSOLIDATED_ATTEMPTS", 9)
    _, part = candidate_records(256)

    async def encode(source):
        return fake_encoder(source)

    prepared = await pdf.consolidate_references(
        [part, text("later reference"), text("last reference")], encoder=encode,
    )
    segments = wire(prepared)
    assert prepared[1].document is not None
    assert prepared[2].document is not None
    (document,) = [doc for _, doc, _ in segments if doc is not None]
    assert b"later reference" in document.pdf
    assert b"last reference" in document.pdf
    assert segments[0][2]["encode_attempts"] <= 9


@pytest.fixture
def unicode_font(monkeypatch):
    path = Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf")
    if not path.is_file():
        pytest.skip("local DejaVu Sans font is unavailable")
    font = pdf._PDFUnicodeFont("HinaPDFPerfUnicode", str(path))
    pdfmetrics.registerFont(font)
    monkeypatch.setattr(pdf, "_font", lambda: font)
    monkeypatch.setattr(pdf, "_fallback_font", lambda index: None)
    return font


@pytest.mark.asyncio
@pytest.mark.parametrize("char", ["\u2002", "\u2003", "\u202f"])
async def test_unicode_space_is_encoded_only_with_exact_roundtrip(unicode_font, char):
    assert unicode_font.face.charToGlyph.get(ord(char))
    source = f"A{char}B"
    document, obs = await pdf.encode_reference(source)
    assert document is not None, obs
    (page,) = PdfReader(io.BytesIO(document.pdf)).pages
    assert page.extract_text().rstrip("\n") == source


@pytest.mark.asyncio
async def test_nbsp_is_never_silently_normalized(unicode_font):
    source = "A\u00a0B"
    part = text(source)
    prepared = await pdf.consolidate_references([part])
    (fragment, document, observation), = wire(prepared)
    if document is None:
        assert fragment is part
        assert fragment["text"] == source
        assert observation["fallback"] == "roundtrip"
    else:
        (page,) = PdfReader(io.BytesIO(document.pdf)).pages
        assert page.extract_text().rstrip("\n") == source


@pytest.mark.asyncio
@pytest.mark.parametrize("char", ["\t", "\u200b", "\u200d", "\u2028", "\U0010ffff"])
async def test_unrepresentable_controls_remain_exact_native_text(char):
    part = text(f"A{char}B")
    prepared = await pdf.consolidate_references([part])
    (fragment, document, observation), = wire(prepared)
    assert document is None
    assert fragment is part
    assert fragment["text"] == f"A{char}B"
    assert observation["fallback"] == "unsupported_glyph"

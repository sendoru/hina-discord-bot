import io
import logging
from pathlib import Path

import pytest
from pypdf import PdfReader
from reportlab.pdfbase import pdfmetrics

from hina_bot.ai import pdf_transport


def test_supplementary_cmap_repairs_odd_and_even_length_scalars_without_touching_bmp():
    subset = [ord("A"), 0x1F602, 0x100000, 0x1F602]
    original = "<00> <0041>\n<01> <1F602>\n<02> <100000>\n<03> <D83DDE02>"
    repaired = "<00> <0041>\n<01> <D83DDE02>\n<02> <DBC0DC00>\n<03> <D83DDE02>"
    assert pdf_transport._repair_supplementary_cmap(original, subset) == repaired
    assert pdf_transport._repair_supplementary_cmap(repaired, subset) == repaired


@pytest.mark.asyncio
@pytest.mark.parametrize("use_fallback", [False, True])
async def test_real_emoji_pdf_roundtrip_without_cmap_warning(monkeypatch, caplog, use_fallback):
    path = Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf")
    if not path.is_file():
        pytest.skip("local DejaVu Sans supplementary Unicode font is unavailable")
    emoji_font = pdf_transport._PDFUnicodeFont(
        f"HinaEmojiTest{int(use_fallback)}", str(path),
    )
    assert emoji_font.face.charToGlyph.get(0x1F602)
    pdfmetrics.registerFont(emoji_font)
    if use_fallback:
        primary = pdf_transport._PDFUnicodeFont("HinaEmojiTestPrimary", "Vera.ttf")
        pdfmetrics.registerFont(primary)
        assert not primary.face.charToGlyph.get(0x1F602)
        monkeypatch.setattr(pdf_transport, "_font", lambda: primary)
        monkeypatch.setattr(pdf_transport, "_fallback_font", lambda index: emoji_font if index == 0 else None)
    else:
        monkeypatch.setattr(pdf_transport, "_font", lambda: emoji_font)

    source = "before 😂 after\nnext 😀 end"
    with caplog.at_level(logging.WARNING, logger="pypdf"):
        document, observation = await pdf_transport.encode_reference(source)
        assert document is not None, observation
        extracted = "\n".join(
            page.extract_text().rstrip("\n") for page in PdfReader(io.BytesIO(document.pdf)).pages
        )
    assert extracted == source
    assert observation["transport"] == "pdf"
    assert not observation["fallback"]
    assert not any("invalid hex string" in record.getMessage().lower() for record in caplog.records)

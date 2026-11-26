"""Local-only document encoding. Only font metrics are shared across calls."""
import asyncio
import base64
import io
import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from threading import Lock
from time import perf_counter

from pypdf import PdfReader
from reportlab.lib.pagesizes import A4
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen.canvas import Canvas

from .reference_context_blocks import PDFReferenceText

_LOCK = Lock()


@dataclass(frozen=True, repr=False)
class GeminiInlinePDF:
    pdf: bytes
    pages: int = 0

    @property
    def data(self):
        return base64.b64encode(self.pdf).decode("ascii")


@lru_cache(maxsize=1)
def _font():
    path = os.getenv("GEMINI_PDF_FONT") or next(
        (str(p) for p in (Path("/usr/share/fonts/truetype/nanum/NanumGothic.ttf"),
                          Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf")) if p.is_file()),
        "Vera.ttf",
    )
    font = TTFont("HinaReferenceFont", path)
    pdfmetrics.registerFont(font)
    return font


def _encode(source):
    font = _font()
    width, height = A4
    size = 11
    stream = io.BytesIO()
    canvas = Canvas(stream, pagesize=A4, pageCompression=1, invariant=1)
    rendered = []
    y = height - 42
    for paragraph in source.split("\n"):
        if not paragraph:
            return None, "empty_line"
        start = 0
        advance = 0
        for index, char in enumerate(paragraph):
            if not char.isprintable() or not font.face.charToGlyph.get(ord(char)):
                return None, "unsupported_glyph"
            glyph_width = font.face.charWidths[ord(char)] * size / 1000
            if advance + glyph_width > width - 84:
                rendered.append(paragraph[start:index])
                advance = 0
                start = index
            advance += glyph_width
        rendered.append(paragraph[start:])
    for line in rendered:
        if y < 42:
            canvas.showPage()
            y = height - 42
        canvas.setFont(font.fontName, size)
        canvas.drawString(42, y, line)
        y -= 16
    canvas.save()
    data = stream.getvalue()
    if len(data) > 512_000:
        return None, "pdf_size_limit"
    pages = PdfReader(io.BytesIO(data)).pages
    recovered = [line for page in pages for line in page.extract_text().splitlines()]
    if recovered != rendered:
        return None, "roundtrip"
    return GeminiInlinePDF(data, len(pages)), ""


async def encode_reference(source):
    started = perf_counter()
    def bounded():
        if not _LOCK.acquire(blocking=False):
            return None, "encoder_busy"
        try:
            return _encode(source)
        except Exception:  # noqa: BLE001 - pre-send fallback at renderer boundary
            # Font, renderer and parser errors must never emit source or PDF bytes.
            return None, "encode_error"
        finally:
            _LOCK.release()

    if not 1 <= len(source) <= 60_000:
        document, reason = None, "source_size_limit"
    else:
        document, reason = await asyncio.to_thread(bounded)
    return document, {
        "transport": "pdf" if document else "text",
        "pages": document.pages if document else 0,
        "pdf_bytes": len(document.pdf) if document else 0,
        "source_chars": len(source),
        "encode_ms": round((perf_counter() - started) * 1000),
        "fallback": reason,
    }


class PreparedPDFReference(PDFReferenceText):
    """Keep the original text mapping while caching a request-local encoding result."""

    def __init__(self, part, document, observation):
        super().__init__(part)
        self.document = document
        self.observation = dict(observation)


def answer_pdf_enabled(settings, *, store=False):
    return getattr(settings, "gemini_pdf_transport", "text") == "pdf" and not store


def text_reference_observation(source, reason):
    return {
        "transport": "text", "source_chars": len(source), "pages": 0,
        "pdf_bytes": 0, "encode_ms": 0, "fallback": reason,
    }


async def prepare_answer_references(messages, *, native_only=False):
    """Encode eligible parts once, before code-tool registration. Keep text for fallback."""
    if not isinstance(messages, list):
        return messages, False
    projected = []
    has_pdf = False
    for message in messages:
        if (not isinstance(message, dict) or message.get("role") != "user"
                or not isinstance(message.get("content"), list)):
            projected.append(message)
            continue
        parts = []
        for part in message["content"]:
            if isinstance(part, PDFReferenceText):
                if native_only:
                    document = None
                    observation = text_reference_observation(
                        part["text"], "code_execution_incompatible",
                    )
                else:
                    document, observation = await encode_reference(part["text"])
                part = PreparedPDFReference(part, document, observation)
                has_pdf = has_pdf or document is not None
            parts.append(part)
        projected.append({**message, "content": parts})
    return projected, has_pdf

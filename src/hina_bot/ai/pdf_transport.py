"""Local-only document encoding. Only font metrics are shared across calls."""
import asyncio
import base64
import io
import json
import os
import re
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

from .reference_context_blocks import REFERENCE_PREFIX, PDFReferenceText

_LOCK = Lock()


def pdf_enabled(settings, domain):
    return (domain in {"answer", "memory", "knowledge"}
            and getattr(settings, "gemini_pdf_transport", "text") == "pdf"
            and getattr(settings, "gemini_pdf_scope", "all") in {"all", domain})


@dataclass(frozen=True, repr=False)
class GeminiInlinePDF:
    pdf: bytes
    pages: int = 0

    @property
    def data(self):
        return base64.b64encode(self.pdf).decode("ascii")


@lru_cache(maxsize=1)
def _font():
    configured = os.getenv("GEMINI_PDF_FONT")
    if configured and not Path(configured).is_file():
        raise ValueError("Expected an installed local TrueType font")
    path = configured or next(
        (str(p) for p in (Path("/usr/share/fonts/truetype/nanum/NanumGothic.ttf"),
                          Path("/mnt/c/Windows/Fonts/malgun.ttf"),
                          Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf")) if p.is_file()),
        "Vera.ttf",
    )
    font = TTFont("HinaReferenceFont", path)
    pdfmetrics.registerFont(font)
    return font


_FALLBACK_FONT_PATHS = (
    "/usr/share/fonts/truetype/nanum/NanumGothic.ttf",
    "/mnt/c/Windows/Fonts/malgun.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/truetype/noto/NotoSans-Regular.ttf",
    "/usr/share/fonts/truetype/noto/NotoSansSymbols2-Regular.ttf",
    "/usr/share/fonts/truetype/noto/NotoSansKR-Regular.ttf",
    "/usr/share/fonts/truetype/noto/NotoSansJP-Regular.ttf",
)


@lru_cache(maxsize=len(_FALLBACK_FONT_PATHS))
def _fallback_font(index):
    path = Path(_FALLBACK_FONT_PATHS[index])
    if not path.is_file():
        return None
    try:
        font = TTFont(f"HinaReferenceFallback{index}", str(path))
        pdfmetrics.registerFont(font)
        return font
    except Exception:  # noqa: BLE001 - unsupported local font is not fatal
        return None


def _encode(source, *, size=11, margin=42, leading=16):
    font = _font()
    by_char = {}

    def font_for(char):
        if char in by_char:
            return by_char[char]
        if not char.isprintable():
            return None
        selected = font if font.face.charToGlyph.get(ord(char)) else None
        if selected is None:
            for index in range(len(_FALLBACK_FONT_PATHS)):
                alternative = _fallback_font(index)
                if alternative is not None and alternative.face.charToGlyph.get(ord(char)):
                    selected = alternative
                    break
        by_char[char] = selected
        return selected
    width, height = A4
    stream = io.BytesIO()
    canvas = Canvas(stream, pagesize=A4, pageCompression=1, invariant=1)
    rendered = []
    y = height - margin
    for paragraph in source.split("\n"):
        if not paragraph:
            return None, "empty_line"
        start = 0
        advance = 0
        # Keep ASCII identifiers, snowflakes and JSON escape sequences readable across lines.
        for match in re.finditer(r"[A-Za-z0-9_.-]+|\\(?:u[0-9a-fA-F]{4}|.)|.", paragraph):
            token = match.group()
            fonts = [font_for(c) for c in token]
            if any(selected is None for selected in fonts):
                return None, "unsupported_glyph"
            token_width = sum(
                selected.face.charWidths[ord(c)]
                for c, selected in zip(token, fonts, strict=True)
            ) * size / 1000
            if token_width > width - 2 * margin:
                return None, "unbreakable_token"
            if advance + token_width > width - 2 * margin:
                rendered.append(paragraph[start:match.start()])
                advance = 0
                start = match.start()
            advance += token_width
        rendered.append(paragraph[start:])
    for line in rendered:
        if y < margin:
            canvas.showPage()
            y = height - margin
        # Consecutive glyph runs keep exact Unicode text; extraction below rejects
        # any font/positioning combination that inserts or drops characters.
        text = canvas.beginText(margin, y)
        start = 0
        while start < len(line):
            selected = by_char[line[start]]
            stop = start + 1
            while stop < len(line) and by_char[line[stop]] is selected:
                stop += 1
            text.setFont(selected.fontName, size)
            text.textOut(line[start:stop])
            start = stop
        canvas.drawText(text)
        y -= leading
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

    def __init__(self, part, document, observation, *, segments=None):
        super().__init__(part)
        self.document = document
        self.observation = dict(observation)
        self.segments = segments or [(part, document, observation)]


def answer_pdf_enabled(settings, *, store=False):
    return pdf_enabled(settings, "answer")


def text_reference_observation(source, reason):
    return {
        "transport": "text", "source_chars": len(source), "pages": 0,
        "pdf_bytes": 0, "encode_ms": 0, "fallback": reason,
    }



# Split only already-authorized JSON reference data; never split instruction or role steps.
# Limit failed encode attempts as well as the number of resulting wire parts.
MAX_FRAGMENT_ATTEMPTS = 15
_SPLIT_REASONS = {"unsupported_glyph", "unbreakable_token", "source_size_limit",
                  "pdf_size_limit", "roundtrip", "empty_line"}


def reference_label(part):
    source = part.get("text", "")
    if not isinstance(source, str) or not source.startswith(REFERENCE_PREFIX):
        return "reference"
    header = source.partition("\n")[0]
    # Fixed labels only: never export arbitrary content as a telemetry key.
    for label in ("화자·상호작용", "직접 답장 문맥", "기억·설정", "대화 문맥",
                  "세계관·참고 지식", "커스텀 이모지"):
        if header == f"{REFERENCE_PREFIX} · {label}):":
            return label
    return "reference"


def _fragment(part, rows, suffix=""):
    header = part["text"].partition("\n")[0]
    if suffix:
        header += " " + suffix
    return PDFReferenceText(
        type="input_text",
        text=header + "\n" + json.dumps(rows, ensure_ascii=False, separators=(",", ":")),
    )


def _split_part(rows, ranges):
    # First split by complete fields; then by contiguous list records.
    fields = list(rows.items())
    if len(fields) > 1:
        middle = len(fields) // 2
        return [(dict(group), {}) for group in (fields[:middle], fields[middle:])]
    if len(fields) == 1:
        key, records = fields[0]
        if isinstance(records, list) and len(records) > 1:
            start, total = ranges.get(key, (0, len(records)))
            middle = len(records) // 2
            return [
                ({key: records[:middle]}, {key: (start, total)}),
                ({key: records[middle:]}, {key: (start + middle, total)}),
            ]
    return []


async def plan_reference(part, *, native_only=False, encoder=None):
    """Return original mapping plus bounded, ordered PDF/text fragments.

    Repeated list keys in fragments are explicitly labelled with absolute record
    ranges; no candidate IDs, records or scalar values are rewritten.
    """
    encoder = encoder or encode_reference
    source = part["text"]
    label = reference_label(part)
    if native_only:
        observation = text_reference_observation(source, "code_execution_incompatible")
        observation.update(section=label, eligible=True)
        return PreparedPDFReference(part, None, observation)
    document, observation = await encoder(source)
    observation = {**observation, "section": label, "eligible": True}
    if document is not None or observation["fallback"] not in _SPLIT_REASONS:
        return PreparedPDFReference(part, document, observation)
    if not source.startswith(REFERENCE_PREFIX):
        return PreparedPDFReference(part, document, observation)
    try:
        rows = json.loads(source.partition("\n")[2])
    except ValueError:
        return PreparedPDFReference(part, document, observation)
    if not isinstance(rows, dict):
        return PreparedPDFReference(part, document, observation)
    attempts = 1
    elapsed = observation["encode_ms"]

    async def visit(fragment_rows, ranges):
        nonlocal attempts, elapsed
        suffix = ""
        if ranges:
            key, (start, total) = next(iter(ranges.items()))
            suffix = f"[records {start + 1}-{start + len(fragment_rows[key])} of {total}; ordered fragment]"
        fragment = _fragment(part, fragment_rows, suffix)
        if attempts >= MAX_FRAGMENT_ATTEMPTS:
            obs = text_reference_observation(fragment["text"], "fragment_budget")
            doc = None
        else:
            attempts += 1
            doc, obs = await encoder(fragment["text"])
        elapsed += obs["encode_ms"]
        obs = {**obs, "section": label, "eligible": True}
        if doc is None and obs["fallback"] in _SPLIT_REASONS and attempts + 2 <= MAX_FRAGMENT_ATTEMPTS:
            children = _split_part(fragment_rows, ranges)
            if children:
                left = await visit(*children[0])
                right = await visit(*children[1])
                return left + right
        return [(fragment, doc, obs)]

    children = _split_part(rows, {})
    if not children:
        return PreparedPDFReference(part, document, observation)
    segments = []
    for child in children:
        segments.extend(await visit(*child))
    first_pdf = next((doc for _, doc, _ in segments if doc is not None), None)
    if first_pdf is None:
        # No useful conversion: retain the exact original wire text.
        observation["encode_ms"] = elapsed
        return PreparedPDFReference(part, None, observation)
    # Fragment headers and repeated keys are transport overhead. Attribute the
    # original character count proportionally to serialized record sizes, and
    # retain the actual fragment length separately. The denominator stays exact.
    weights = [len(fragment["text"].partition("\n")[2]) for fragment, _, _ in segments]
    total_weight = sum(weights)
    consumed_weight = allocated = 0
    for weight, (_, _, obs) in zip(weights, segments, strict=True):
        consumed_weight += weight
        boundary = len(source) * consumed_weight // total_weight
        obs["wire_text_chars"] = obs["source_chars"]
        obs["source_chars"] = boundary - allocated
        allocated = boundary
    segments[0][2]["encode_ms"] += elapsed - sum(obs["encode_ms"] for _, _, obs in segments)
    return PreparedPDFReference(part, first_pdf, observation, segments=segments)


def native_reference_observation(part):
    """Content-free explanation for intentionally native input."""
    if not isinstance(part, dict) or part.get("type") != "input_text":
        return None
    source = part.get("text")
    if not isinstance(source, str):
        return None
    label = reference_label(part)
    reason = "current_or_native_text"
    if label in {"화자·상호작용", "직접 답장 문맥"}:
        reason = "protected_reference"
    obs = text_reference_observation(source, reason)
    obs.update(section=label if label != "reference" else "native_text", eligible=False)
    return obs


def transport_coverage(observations):
    eligible = sum(o["source_chars"] for o in observations if o.get("eligible", True))
    converted = sum(o["source_chars"] for o in observations if o["transport"] == "pdf")
    total = sum(o["source_chars"] for o in observations)
    return {
        "source_chars": total,
        "eligible_chars": eligible,
        "pdf_chars": converted,
        "pages": sum(o["pages"] for o in observations if o["transport"] == "pdf"),
        "pdf_bytes": sum(o["pdf_bytes"] for o in observations if o["transport"] == "pdf"),
        "encode_ms": sum(o["encode_ms"] for o in observations),
        "eligible_coverage": round(converted / eligible, 4) if eligible else None,
        "total_coverage": round(converted / total, 4) if total else None,
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
                part = await plan_reference(part, native_only=native_only)
                has_pdf = has_pdf or part.document is not None
            parts.append(part)
        projected.append({**message, "content": parts})
    return projected, has_pdf

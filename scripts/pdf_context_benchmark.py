# /// script
# requires-python = ">=3.11"
# dependencies = ["reportlab>=4,<5", "pypdf>=6,<7", "httpx>=0.27,<1"]
# ///
"""Measure a synthetic, in-memory PDF. Requests terminate in a local mock."""
import argparse
import base64
import io
import json
from pathlib import Path
from time import perf_counter

import httpx
from pypdf import PdfReader
from reportlab.lib.pagesizes import A4
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen.canvas import Canvas


def measure(font_path: Path, rows: int, size: int, sample: str) -> dict:
    font = TTFont("Benchmark", str(font_path))
    pdfmetrics.registerFont(font)
    lines = [f"c{i} {10**18+i} {sample}" for i in range(rows)]
    started = perf_counter()
    stream = io.BytesIO()
    canvas = Canvas(stream, pagesize=A4, invariant=True, pageCompression=True)
    width, height = A4
    y = height - 36
    missing = set()
    for line in lines:
        missing.update(c for c in line if not font.face.charToGlyph.get(ord(c)))
        if pdfmetrics.stringWidth(line, font.fontName, size) > width - 72:
            raise ValueError("Line exceeds printable width")
        if y < 36:
            canvas.showPage()
            y = height - 36
        canvas.setFont(font.fontName, size)
        canvas.drawString(36, y, line)
        y -= size * 1.5
    canvas.save()
    data = stream.getvalue()
    encoded_ms = (perf_counter() - started) * 1000
    pages = PdfReader(io.BytesIO(data)).pages
    recovered = [line for page in pages for line in page.extract_text().splitlines()]
    encoded = base64.b64encode(data).decode("ascii")
    bodies = {
        "interactions": {"input": [{"type": "document", "mime_type": "application/pdf", "data": encoded}]},
        "generateContent": {"contents": [{"role": "user", "parts": [{"inlineData": {"mimeType": "application/pdf", "data": encoded}}]}]},
    }
    lengths = {}
    def capture(request):
        body = json.loads(request.content)
        if request.url.path == "/interactions":
            part = body["input"][0]
        else:
            part = body["contents"][0]["parts"][0]["inlineData"]
        assert base64.b64decode(part["data"], validate=True) == data
        lengths[request.url.path] = len(request.content)
        return httpx.Response(200)

    with httpx.Client(transport=httpx.MockTransport(capture)) as client:
        for api, body in bodies.items():
            client.post(f"https://mock.invalid/{api}", json=body).raise_for_status()
    return dict(pages=len(pages), rows=rows, font_pt=size, rows_per_page=rows/len(pages),
                pdf_bytes=len(data), request_bytes=lengths, encode_ms=round(encoded_ms, 2),
                missing_glyphs=len(missing), exact_roundtrip=recovered == lines,
                eligible=not missing and recovered == lines, billing=None, model_fidelity=None)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--font", required=True, type=Path)
    parser.add_argument("--rows", type=int, choices=range(1, 1001), metavar="1..1000", default=120)
    parser.add_argument("--size", type=int, choices=(10, 12, 14), default=12)
    args = parser.parse_args()
    for sample in ("alpha = 42", "히나 日本語 alpha", "히나 日本語 🙂"):
        print(json.dumps(measure(args.font, args.rows, args.size, sample)))

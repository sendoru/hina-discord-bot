"""Offline production-encoder density comparison; never contacts a provider."""
import argparse
import base64
import json
import os
from pathlib import Path
from time import perf_counter

import httpx

from hina_bot.ai.pdf_transport import _encode, _font


def measure(font_path: Path, rows: int, size: int, sample: str) -> dict:
    os.environ["GEMINI_PDF_FONT"] = str(font_path)
    _font.cache_clear()
    source = json.dumps(
        {"records": [{"id": str(10**18+i), "content": sample} for i in range(rows)]},
        ensure_ascii=False, separators=(",", ":"),
    )
    started = perf_counter()
    margin, leading = (42, 16) if size == 11 else (36, size * 1.4)
    document, reason = _encode(source, size=size, margin=margin, leading=leading)
    encoded_ms = round((perf_counter() - started) * 1000, 2)
    result = {
        "rows": rows, "source_chars": len(source), "font_pt": size, "margin_pt": margin,
        "leading_pt": leading, "encode_ms": encoded_ms, "fallback": reason,
        "eligible": document is not None, "billing": None, "model_fidelity": None,
    }
    if document is None:
        return result
    bodies = {
        "interactions": {"input": [{"type": "document", "mime_type": "application/pdf", "data": document.data}]},
        "generateContent": {"contents": [{"role": "user", "parts": [{"inlineData": {"mimeType": "application/pdf", "data": document.data}}]}]},
    }
    lengths = {}

    def capture(request):
        body = json.loads(request.content)
        if request.url.path == "/interactions":
            part = body["input"][0]
        else:
            part = body["contents"][0]["parts"][0]["inlineData"]
        assert base64.b64decode(part["data"], validate=True) == document.pdf
        lengths[request.url.path] = len(request.content)
        return httpx.Response(200)

    with httpx.Client(transport=httpx.MockTransport(capture)) as client:
        for api, body in bodies.items():
            client.post(f"https://mock.invalid/{api}", json=body).raise_for_status()
    result.update(
        pages=document.pages, pdf_bytes=len(document.pdf), request_bytes=lengths,
        chars_per_page=round(len(source)/document.pages, 1), exact_roundtrip=True,
    )
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--font", required=True, type=Path)
    parser.add_argument("--rows", type=int, choices=range(1, 1001), default=120)
    parser.add_argument("--size", type=int, nargs="+", choices=(8, 9, 10, 11, 12, 14), default=[9, 10, 11])
    args = parser.parse_args()
    for sample in ("alpha = 42", "히나 日本語 alpha", "히나 日本語 🙂"):
        for size in args.size:
            print(json.dumps(measure(args.font, args.rows, size, sample)))

"""Inspect compact static lore size; optionally count real Gemini input tokens.

The --count-tokens call measures the compiled lore document alone, not the complete
answer prompt or any provider-side cache discount. No API call without that flag.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

import httpx
from dotenv import load_dotenv

from hina_bot.core.compact_lore import compile_compact_lore
from hina_bot.core.lore import LoreIndex


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--lore-path", default="", help="Optional alternate reviewed lore JSONL")
    parser.add_argument("--exclude-community", action="store_true")
    parser.add_argument("--count-tokens", action="store_true",
                        help="Call Gemini countTokens (requires GEMINI_API_KEY)")
    parser.add_argument("--model", default="",
                        help="Gemini answer model ID for --count-tokens")
    args = parser.parse_args(argv)
    lore = compile_compact_lore(
        LoreIndex.load(args.lore_path),
        include_community=not args.exclude_community,
    )
    result = {
        "mode": "full",
        "items": lore.items,
        "audience_only_excluded": lore.excluded_audience_items,
        "compact_lore_chars": lore.chars,
        "compact_lore_utf8_bytes": len(lore.text.encode("utf-8")),
    }
    if args.count_tokens:
        if not args.model:
            parser.error("--model is required with --count-tokens")
        load_dotenv(Path.cwd() / ".env.local", override=False)
        load_dotenv(Path.cwd() / ".env", override=False)
        key = os.getenv("GEMINI_API_KEY", "").strip()
        if not key:
            parser.error("GEMINI_API_KEY is required with --count-tokens")
        model = args.model.removeprefix("models/")
        response = httpx.post(
            f"https://generativelanguage.googleapis.com/v1beta/models/{model}:countTokens",
            headers={"x-goog-api-key": key},
            json={"contents": [{"role": "user", "parts": [{"text": lore.text}]}]},
            timeout=30.0,
        )
        response.raise_for_status()
        result["model"] = model
        result["lore_only_input_tokens"] = int(response.json()["totalTokens"])
        result["note"] = "Lore alone, not entire answer prompt; excludes cached-token pricing."
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

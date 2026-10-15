"""Test-only reader for purpose-scoped JSON reference input parts."""
from __future__ import annotations

import json

from hina_bot.ai.reference_context_blocks import REFERENCE_PREFIX


def read_reference_context(content):
    """Recover the reference dictionary without treating visual notes as context."""
    if isinstance(content, str):
        return json.loads(content.split("\n", 1)[1])
    merged = {}
    for block in content:
        if (
            isinstance(block, dict)
            and block.get("type") == "input_text"
            and str(block.get("text") or "").startswith(REFERENCE_PREFIX)
        ):
            part = json.loads(block["text"].split("\n", 1)[1])
            assert not (part.keys() & merged.keys())
            merged.update(part)
    assert merged
    return merged

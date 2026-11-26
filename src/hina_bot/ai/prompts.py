"""Load static prompt resources with explicit native/document component boundaries."""

from functools import cache
from importlib.resources import files

# Ordered resources. None means native policy; named entries are app guidance.
# The original complete prompt remains available in the original order.
_PROMPT_COMPONENTS = {
    "dialogue_context.md": (
        (None, "dialogue_context.md"),
        ("dialogue_history", "dialogue_context.guidance.1.md"),
        (None, "dialogue_context.native.2.md"),
    ),
    "structured_memory.md": (
        (None, "structured_memory.md"),
        ("memory_scope", "structured_memory.guidance.1.md"),
        (None, "structured_memory.native.2.md"),
        ("memory_annotations", "structured_memory.guidance.3.md"),
        (None, "structured_memory.native.4.md"),
        ("memory_relationship_profiles", "structured_memory.guidance.5.md"),
        (None, "structured_memory.native.6.md"),
    ),
    "turn_response.md": (
        ("turn_behavior", "turn_response.md"),
        (None, "turn_response.native.1.md"),
    ),
    "vision_input.md": (
        (None, "vision_input.md"),
        ("vision_context", "vision_input.guidance.1.md"),
        (None, "vision_input.native.2.md"),
        ("vision_interpretation", "vision_input.guidance.3.md"),
        (None, "vision_input.native.4.md"),
    ),
    "final_output.md": (
        (None, "final_output.md"),
        ("final_response_style", "final_output.guidance.1.md"),
    ),
}


@cache
def _resource(name: str, section: str) -> str:
    return files("hina_bot").joinpath("prompts", section, name).read_text(encoding="utf-8")


@cache
def prompt_components(name: str) -> tuple[tuple[str | None, str], ...]:
    """Ordered native policy and explicitly document-eligible guidance."""
    return tuple(
        (label, _resource(filename, "integration"))
        for label, filename in _PROMPT_COMPONENTS.get(name, ((None, name),))
    )


@cache
def load_prompt(name: str, *, section: str = "integration") -> str:
    if section == "integration":
        return "\n".join(source for _, source in prompt_components(name))
    return _resource(name, section)


def document_prompt_components(name: str) -> tuple[tuple[str, str], ...]:
    return tuple((label, source) for label, source in prompt_components(name) if label is not None)

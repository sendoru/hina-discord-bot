"""Shared operation options. Native payloads and replay remain adapter-owned."""

from hina_bot.core.config import gemini_store_enabled

from .openai_capabilities import reasoning_options


def operation_options(settings, domain, model, tier, *, provider=None, thinking_level=None):
    provider = provider or getattr(settings, "provider", None)
    options = reasoning_options(settings, model, tier, provider=provider)
    options["store"] = gemini_store_enabled(settings, domain, provider=provider)
    if provider == "gemini" and thinking_level is not None:
        options["thinking_level"] = thinking_level
    if provider == "openai" and domain == "answer":
        options["include"] = ["reasoning.encrypted_content"]
    return options


def stored_interaction_id(response) -> str:
    """Stored Gemini interaction handle, empty for stateless responses."""
    return str(getattr(response, "_hina_interaction_id", "") or "")


def response_usage(response) -> dict:
    usage = getattr(response, "usage", None)
    return {
        "input_tokens": getattr(usage, "input_tokens", None),
        "output_tokens": getattr(usage, "output_tokens", None),
        "total_tokens": getattr(usage, "total_tokens", None),
        "cached_tokens": getattr(
            getattr(usage, "input_tokens_details", None), "cached_tokens", None),
        "reasoning_tokens": getattr(
            getattr(usage, "output_tokens_details", None), "reasoning_tokens", None),
    }

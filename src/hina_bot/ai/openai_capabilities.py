"""Explicit Responses capabilities for the model families used by the bot."""

import re


def model_family(model: str) -> str:
    return re.sub(r"-\d{4}-\d{2}-\d{2}$", "", model.lower())


_REASONING = {"gpt-5", "gpt-5-mini", "gpt-5-nano", "gpt-5.1", "gpt-5.2",
              "gpt-5.4", "gpt-5.4-mini", "gpt-5.4-nano", "o3", "o3-mini", "o4-mini"}
_VISION = (_REASONING - {"o3-mini"}) | {
    "gpt-4o", "gpt-4o-mini", "gpt-4.1", "gpt-4.1-mini", "gpt-4.1-nano",
}


def supports_reference_image(model: str) -> bool:
    return model_family(model) in _VISION


def reasoning_options(settings, model: str, tier: str, *, provider=None) -> dict:
    if (provider or settings.provider) != "openai" or model_family(model) not in _REASONING:
        return {}
    prefix = {"fast": "fast_", "smart": "smart_"}.get(tier, "")
    effort = getattr(settings, f"openai_{prefix}reasoning_effort")
    return {} if effort == "default" else {"reasoning": {"effort": effort}}

"""Explicit Responses capabilities for the model families used by the bot."""

import re


def model_family(model: str) -> str:
    return re.sub(r"-\d{4}-\d{2}-\d{2}$", "", model.lower())


_STANDARD = frozenset({"low", "medium", "high"})
_GPT6 = _STANDARD | {"xhigh", "max"}
_GPT54 = _STANDARD | {"none", "xhigh"}
_REASONING = {
    **{name: _STANDARD | {"minimal"} for name in ("gpt-5", "gpt-5-mini", "gpt-5-nano")},
    "gpt-5.1": _STANDARD | {"none"},
    "gpt-5.2": _GPT54,
    **{name: _GPT54 for name in ("gpt-5.4", "gpt-5.4-mini", "gpt-5.4-nano")},
    **{name: _STANDARD for name in ("o3", "o3-mini", "o4-mini")},
    **{name: _GPT6 | {"none"} for name in ("gpt-6-luna", "gpt-6-sol", "gpt-6.1-terra")},
    **{name: _GPT6 for name in ("gpt-6-astra", "gpt-6.1-sol", "gpt-6.2-astra", "gpt-6.2-sol")},
}
_VISION = (set(_REASONING) - {"o3-mini"}) | {
    "gpt-4o", "gpt-4o-mini", "gpt-4.1", "gpt-4.1-mini", "gpt-4.1-nano",
}


def reference_image_support(model: str) -> bool | None:
    """None denotes an unlisted model. Only affirmative support permits a reference."""
    family = model_family(model)
    if family in _VISION:
        return True
    if family == "o3-mini":
        return False
    return None


def supports_reference_image(model: str) -> bool:
    return reference_image_support(model) is True


def reasoning_options(settings, model: str, tier: str, *, provider=None) -> dict:
    if (provider or getattr(settings, "provider", None)) != "openai":
        return {}
    supported = _REASONING.get(model_family(model))
    if supported is None:
        return {}
    prefix = {"fast": "fast_", "smart": "smart_"}.get(tier, "")
    effort = getattr(settings, f"openai_{prefix}reasoning_effort")
    if effort == "default":
        return {}
    if effort not in supported:
        raise ValueError(f"OpenAI model {model} does not support reasoning effort {effort}")
    return {"reasoning": {"effort": effort}}

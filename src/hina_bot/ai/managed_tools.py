"""Semantic provider-managed capabilities exposed to conversational answers."""

from __future__ import annotations

from enum import StrEnum

from .prompts import load_prompt


class ManagedToolCapability(StrEnum):
    CODE_EXECUTION = "code_execution"


CODE_EXECUTION_POLICY = load_prompt("code_execution.md")


_PROVIDER_BINDINGS = {
    "gemini": {
        ManagedToolCapability.CODE_EXECUTION: {"type": "code_execution"},
    },
    "openai": {
        ManagedToolCapability.CODE_EXECUTION: {
            "type": "code_interpreter",
            "container": {"type": "auto"},
        },
    },
}


def managed_tool_config(
    provider: str,
    capabilities: tuple[ManagedToolCapability, ...] = (
        ManagedToolCapability.CODE_EXECUTION,
    ),
) -> list[dict]:
    """Map semantic managed-tool capabilities to provider-native tool declarations."""

    bindings = _PROVIDER_BINDINGS.get(provider, {})
    return [
        dict(bindings[capability])
        for capability in capabilities
        if capability in bindings
    ]


def search_tool_choice(provider: str, search_mode: str, *, has_managed_tools: bool):
    """Preserve required-web semantics when other provider-managed tools coexist."""

    if search_mode == "required":
        if provider == "openai":
            return {"type": "web_search"}
        return "required"
    if has_managed_tools:
        return "auto"
    return None


__all__ = [
    "CODE_EXECUTION_POLICY",
    "ManagedToolCapability",
    "managed_tool_config",
    "search_tool_choice",
]

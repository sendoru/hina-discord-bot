from types import SimpleNamespace

import pytest

from hina_bot.ai.delegated_code_execution import attach_delegated_code_execution
from hina_bot.ai.managed_tools import ManagedToolCapability, managed_tool_support
from hina_bot.ai.openai_capabilities import reference_image_support
from hina_bot.ai.provider_contract import operation_options


@pytest.mark.parametrize("domain", ["answer", "memory", "knowledge", "classifier", "output_repair"])
@pytest.mark.parametrize("provider", ["gemini", "openai", "openrouter"])
def test_options_follow_operation_provider(domain, provider):
    settings = SimpleNamespace(provider="gemini", gemini_store_interactions=True,
                               openai_fast_reasoning_effort="low")
    options = operation_options(settings, domain, "gpt-5-mini", "fast",
                                provider=provider, thinking_level="minimal")
    expected = {"store": provider == "gemini"}
    if provider == "gemini":
        expected["thinking_level"] = "minimal"
    elif provider == "openai":
        expected["reasoning"] = {"effort": "low"}
    if provider in {"openai", "openrouter"} and domain == "answer":
        expected["include"] = ["reasoning.encrypted_content"]
    assert options == expected


def test_unknown_capabilities_and_worker_boundary():
    assert reference_image_support("unlisted-model") is None
    assert reference_image_support("o3-mini") is False
    assert reference_image_support("gpt-4o") is True
    assert managed_tool_support("unlisted-provider", ManagedToolCapability.URL_CONTEXT) is None
    assert managed_tool_support("openai", ManagedToolCapability.URL_CONTEXT) is False
    with pytest.raises(ValueError, match="Gemini"):
        attach_delegated_code_execution(None, client=SimpleNamespace(provider_name="openai"),
                                        usage=None, model="fixture", timeout=1, thinking_level="low")

def test_openrouter_tier_effort_inherits_or_overrides():
    settings = SimpleNamespace(provider="openrouter", openrouter_reasoning_effort="low",
                               openrouter_fast_reasoning_effort="none",
                               openrouter_smart_reasoning_effort="inherit")
    assert operation_options(settings, "answer", "fixture/model", "fast")["reasoning"] == {"enabled": False}
    assert operation_options(settings, "answer", "fixture/model", "smart")["reasoning"] == {"effort": "low"}
    settings.openrouter_smart_reasoning_effort = "default"
    assert "reasoning" not in operation_options(settings, "answer", "fixture/model", "smart")

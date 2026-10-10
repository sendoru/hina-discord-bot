from hina_bot.ai.llm import POLICY
from hina_bot.ai.managed_tools import CODE_EXECUTION_POLICY
from hina_bot.ai.prompts import load_prompt
from hina_bot.ai.request_assembly import (
    CURRENT_CHANNEL_SCOPE_POLICY,
    CURRENT_INTERACTION_POLICY,
    CURRENT_SPEAKER_POLICY,
    FINAL_OUTPUT_CHECK_POLICY,
    LIVE_INFORMATION_POLICY,
    REFERENCE_CONTINUITY_POLICY,
    REFERENCE_PROVENANCE_POLICY,
    REPLY_CONTINUITY_POLICY,
    STRUCTURED_MEMORY_POLICY,
    TARGET_HISTORY_POLICY,
    TURN_RESPONSE_POLICY,
    WEB_SEARCH_POLICY,
    WORLD_CORE_POLICY,
    WORLD_FACT_DETAIL_POLICY,
    WORLD_WEB_SEARCH_POLICY,
)
from hina_bot.ai.runtime_llm import GENERAL_RP_OUTPUT_POLICY
from hina_bot.ai.vision import VISION_INPUT_POLICY


def test_static_answer_policies_are_loaded_from_named_resources():
    expected = {
        "base.md": POLICY,
        "continuity.md": REFERENCE_CONTINUITY_POLICY,
        "reply_continuity.md": REPLY_CONTINUITY_POLICY,
        "reference_provenance.md": REFERENCE_PROVENANCE_POLICY,
        "current_speaker.md": CURRENT_SPEAKER_POLICY,
        "current_interaction.md": CURRENT_INTERACTION_POLICY,
        "world_core.md": WORLD_CORE_POLICY,
        "turn_response.md": TURN_RESPONSE_POLICY,
        "final_output.md": FINAL_OUTPUT_CHECK_POLICY,
        "live_information.md": LIVE_INFORMATION_POLICY,
        "web_search.md": WEB_SEARCH_POLICY,
        "world_web_search.md": WORLD_WEB_SEARCH_POLICY,
        "world_fact.md": WORLD_FACT_DETAIL_POLICY,
        "current_channel_scope.md": CURRENT_CHANNEL_SCOPE_POLICY,
        "target_history.md": TARGET_HISTORY_POLICY,
        "structured_memory.md": STRUCTURED_MEMORY_POLICY,
        "general_rp_output.md": GENERAL_RP_OUTPUT_POLICY,
        "code_execution.md": CODE_EXECUTION_POLICY,
        "vision_input.md": VISION_INPUT_POLICY,
    }

    for name, policy in expected.items():
        assert policy == load_prompt(name)
        assert policy.endswith("\n")


def test_prompt_loader_caches_resource_text():
    assert load_prompt("base.md") is load_prompt("base.md")

from hina_bot.ai.llm import POLICY
from hina_bot.ai.managed_tools import CODE_EXECUTION_POLICY
from hina_bot.ai.memory_extraction import (
    ANNOTATION_FIELD_RULES,
    DIRECTIVE_VERIFICATION_POLICY,
    SHADOW_EXTRACTION_POLICY,
)
from hina_bot.ai.prompts import load_prompt
from hina_bot.ai.request_assembly import (
    CURRENT_CHANNEL_SCOPE_POLICY,
    DIALOGUE_CONTEXT_POLICY,
    DM_SPACE_POLICY,
    FINAL_OUTPUT_CHECK_POLICY,
    GUILD_SPACE_POLICY,
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
from hina_bot.ai.vision import VISION_INPUT_POLICY


def test_static_answer_policies_are_loaded_from_named_resources():
    expected = {
        "base.md": POLICY,
        "continuity.md": REFERENCE_CONTINUITY_POLICY,
        "reply_continuity.md": REPLY_CONTINUITY_POLICY,
        "reference_provenance.md": REFERENCE_PROVENANCE_POLICY,
        "dialogue_context.md": DIALOGUE_CONTEXT_POLICY,
        "conversation_dm.md": DM_SPACE_POLICY,
        "conversation_guild.md": GUILD_SPACE_POLICY,
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
        "code_execution.md": CODE_EXECUTION_POLICY,
        "vision_input.md": VISION_INPUT_POLICY,
    }

    for name, policy in expected.items():
        assert policy == load_prompt(name)


def test_turn_response_policy_distinguishes_tool_use_from_unnecessary_calls():
    assert "[도구 사용 판단]" in TURN_RESPONSE_POLICY
    assert "확인 불가능하다고 단정하거나 추측하기 전에" in TURN_RESPONSE_POLICY
    assert "다른 대화 참여자의 추측이나 기능 제한 주장" in TURN_RESPONSE_POLICY
    assert "전체를 확인했다고 말하거나 정확한 통계를 만들어내지 마세요" in (
        TURN_RESPONSE_POLICY
    )
    assert "잡담과 일반적인 답변에는 불필요하게 도구를 호출하지 마세요" in (
        TURN_RESPONSE_POLICY
    )


def test_prompt_loader_caches_resource_text():
    assert load_prompt("base.md") is load_prompt("base.md")


def test_memory_extraction_policies_are_loaded_from_named_resources():
    annotation = load_prompt("annotation_fields.md", section="memory")
    extraction = load_prompt("extraction.md", section="memory")
    verification = load_prompt("directive_verification.md", section="memory")

    assert ANNOTATION_FIELD_RULES == annotation
    assert extraction.count("{{ANNOTATION_FIELD_RULES}}") == 1
    assert SHADOW_EXTRACTION_POLICY == extraction.replace(
        "{{ANNOTATION_FIELD_RULES}}", annotation
    )
    assert DIRECTIVE_VERIFICATION_POLICY == verification

"""Guard prompt assembly ownership after merging Discord dialogue policies."""

from importlib.resources import files

from hina_bot.ai.request_assembly import DIALOGUE_CONTEXT_POLICY


def test_dialogue_policy_preserves_both_source_sections():
    assert DIALOGUE_CONTEXT_POLICY.count("[현재 화자와 제3자]") == 1
    assert DIALOGUE_CONTEXT_POLICY.count("[현재 메시지의 상호작용 구조]") == 1
    assert "current_speaker" in DIALOGUE_CONTEXT_POLICY
    assert "reply_target" in DIALOGUE_CONTEXT_POLICY
    assert "trigger_kind" in DIALOGUE_CONTEXT_POLICY
    root = files("hina_bot").joinpath("prompts/integration")
    assert not root.joinpath("current_speaker.md").is_file()
    assert not root.joinpath("current_interaction.md").is_file()


def test_space_prompt_only_describes_environment_and_preserves_dm_affection():
    root = files("hina_bot").joinpath("prompts/integration")
    dm = root.joinpath("conversation_dm.md").read_text(encoding="utf-8")
    guild = root.joinpath("conversation_guild.md").read_text(encoding="utf-8")
    assert "강한 애정 표현" in dm
    assert "특별한 관계·호칭을 가정" not in dm
    assert "자동 응답 채널에서는" in guild
    assert "현재 화자와 주변 참여자를 구별" not in guild

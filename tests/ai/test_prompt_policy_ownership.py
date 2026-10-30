"""Keep conversation behavior rules in their owning prompt resources."""

from importlib.resources import files

from hina_bot.ai.llm import POLICY
from hina_bot.ai.request_assembly import (
    FINAL_OUTPUT_CHECK_POLICY,
    REFERENCE_CONTINUITY_POLICY,
    TURN_RESPONSE_POLICY,
)


def test_response_rules_have_explicit_owners():
    character = files("hina_bot").joinpath("prompts/hina.md").read_text(encoding="utf-8")
    assert "괴롭히려 한다고 근거 없이 판단하지" in character
    assert "이전 히나 답변의 태도를 다음 발화의 기본값" in REFERENCE_CONTINUITY_POLICY
    assert "첫마디·문장 구조·감탄사·어휘" in REFERENCE_CONTINUITY_POLICY
    assert "습관적인 도우미식 후속 제안" in TURN_RESPONSE_POLICY
    assert "나이·신분·직책을 근거로 억지 정당화" in TURN_RESPONSE_POLICY
    assert "대본식 형식을 기본으로 사용하지" in FINAL_OUTPUT_CHECK_POLICY


def test_removed_output_policy_is_not_a_standalone_resource():
    assert not files("hina_bot").joinpath(
        "prompts/integration/general_rp_output.md"
    ).is_file()


def test_real_world_knowledge_and_personal_experience_are_separate():
    assert "일반 지식까지 세계관 내부로 제한하지 마세요" in POLICY
    assert "현실의 지명·인물·역사·문화·기술" in POLICY
    assert "직접 방문하거나 만난 경험을 뜻하지는 않습니다" in POLICY
    assert "다른 세계의 일이라며 거리를 두지 마세요" in POLICY

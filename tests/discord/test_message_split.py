"""Regression tests for model-guided, Discord-safe reply delivery."""

import pytest

from hina_bot.discord.message_split import (
    DISCORD_MESSAGE_LIMIT,
    MESSAGE_SPLIT_DELIMITER,
    MODEL_MESSAGE_TARGET_CHARS,
    model_segments,
    prepare_discord_answer,
    utf16_length,
)


def test_short_answer_is_unchanged():
    text = "안녕! **오늘도** 잘 지냈어? `f_*`"
    assert prepare_discord_answer(text) == (text, [text])


def test_explicit_model_boundaries_are_consumed_and_cleaned_for_memory():
    text = f"첫 번째 설명\n\n{MESSAGE_SPLIT_DELIMITER}\n\n두 번째 설명"
    cleaned, messages = prepare_discord_answer(text)
    assert messages == ["첫 번째 설명", "두 번째 설명"]
    assert cleaned == "첫 번째 설명\n\n두 번째 설명"
    assert MESSAGE_SPLIT_DELIMITER not in cleaned


def test_only_standalone_delimiter_counts_and_empty_segments_are_ignored():
    text = (
        "첫 번째\n<<<MESSAGE_SPLIT>>>not delimiter\n"
        "<<<MESSAGE_SPLIT>>>\n<<<MESSAGE_SPLIT>>>\n마지막"
    )
    assert model_segments(text) == [
        "첫 번째\n<<<MESSAGE_SPLIT>>>not delimiter", "마지막",
    ]


def test_delimiter_within_fenced_code_remains_literal():
    text = (
        "예시:\n```text\n<<<MESSAGE_SPLIT>>>\n```\n"
        "<<<MESSAGE_SPLIT>>>\n완료"
    )
    cleaned, parts = prepare_discord_answer(text)
    assert parts == ["예시:\n```text\n<<<MESSAGE_SPLIT>>>\n```", "완료"]
    assert "<<<MESSAGE_SPLIT>>>" in cleaned  # Literal user-facing code, not a transport hint.


def test_malformed_boundary_inside_emphasis_does_not_split_or_leak():
    text = "**강조된 부분\n<<<MESSAGE_SPLIT>>>\n계속 강조**"
    cleaned, parts = prepare_discord_answer(text)
    assert len(parts) == 1
    assert MESSAGE_SPLIT_DELIMITER not in cleaned


def test_model_segments_over_limit_use_safe_fallback():
    text = "가" * 2400 + "\n<<<MESSAGE_SPLIT>>>\n" + "마지막"
    cleaned, parts = prepare_discord_answer(text)
    assert len(parts) >= 3
    assert parts[-1] == "마지막"
    assert len(cleaned) > 2400
    assert all(0 < utf16_length(part) <= DISCORD_MESSAGE_LIMIT for part in parts)


def test_markdown_emphasis_is_closed_and_reopened_at_forced_boundaries():
    text = "**" + "가나다라마바사 " * 350 + "**"
    cleaned, parts = prepare_discord_answer(text)
    assert cleaned == text
    assert len(parts) > 1
    assert all(part.count("**") % 2 == 0 for part in parts)
    assert all(utf16_length(part) <= DISCORD_MESSAGE_LIMIT for part in parts)


def test_long_fenced_code_block_remains_renderable_per_message():
    text = "다음 코드야:\n```python\n" + "print('안녕')\n" * 270 + "```"
    cleaned, parts = prepare_discord_answer(text)
    assert cleaned == text
    assert len(parts) > 2
    assert all(utf16_length(part) <= DISCORD_MESSAGE_LIMIT for part in parts)
    assert all(part.count("```") % 2 == 0 for part in parts)
    assert any(part.startswith("```python\n") for part in parts[1:])


def test_utf16_surrogate_pairs_and_custom_emoji_are_not_split():
    emoji = "<:wave:123456789012345678>"
    text = "😀" * 1050 + emoji + "😀" * 200
    cleaned, parts = prepare_discord_answer(text)
    assert cleaned == text
    assert len(parts) > 1
    assert all(utf16_length(part) <= DISCORD_MESSAGE_LIMIT for part in parts)
    assert sum(emoji in part for part in parts) == 1
    assert all("<:wave:" not in part or emoji in part for part in parts)


def test_paragraph_boundaries_take_priority_over_midword_cuts():
    text = "첫 문단입니다.\n\n" + ("나 " * 1400)
    _, parts = prepare_discord_answer(text)
    assert parts[0] == "첫 문단입니다."
    assert len(parts) > 1


def test_output_prompt_and_parser_share_character_agnostic_marker():
    from hina_bot.ai.request_assembly import FINAL_OUTPUT_CHECK_POLICY

    assert MESSAGE_SPLIT_DELIMITER in FINAL_OUTPUT_CHECK_POLICY
    assert str(MODEL_MESSAGE_TARGET_CHARS // 100) in FINAL_OUTPUT_CHECK_POLICY.replace(",", "")
    assert "LaTeX" in FINAL_OUTPUT_CHECK_POLICY
    assert "인라인 코드" in FINAL_OUTPUT_CHECK_POLICY
    assert "HINA_MESSAGE_SPLIT" not in FINAL_OUTPUT_CHECK_POLICY


@pytest.mark.parametrize("limit", [0, -1])
def test_invalid_limit_is_rejected(limit):
    with pytest.raises(ValueError):
        prepare_discord_answer("안녕", limit=limit)

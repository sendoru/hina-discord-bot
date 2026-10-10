from hina_bot.ai.vision import VisualInput
from hina_bot.core.routing import Scope
from hina_bot.discord.bot import _bare_call_reply, _has_strong_visual_context


def _visual(reference_strength: str) -> VisualInput:
    return VisualInput(
        b"image",
        "image/png",
        "attachment",
        reference_strength=reference_strength,
    )


def test_only_strong_visual_context_bypasses_bare_call_reply():
    assert not _has_strong_visual_context(())
    assert _has_strong_visual_context((_visual("current_message"),))
    assert _has_strong_visual_context((_visual("explicit_reply"),))
    assert not _has_strong_visual_context((_visual("passive_recent"),))


def test_bare_call_reply_uses_configured_values():
    ordinary = "기본 빈 호출 응답"
    special = "특수 DM 빈 호출 응답"

    assert _bare_call_reply(Scope(1, 10, 100), 100, ordinary, special) == ordinary
    assert _bare_call_reply(Scope(None, 10, 101), 100, ordinary, special) == ordinary
    assert _bare_call_reply(Scope(None, 10, 100), 100, ordinary, special) == special
    assert _bare_call_reply(Scope(None, 10, 100), 100, ordinary, "") == ordinary

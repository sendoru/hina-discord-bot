from hina_bot.ai.vision import VisualInput
from hina_bot.core.routing import Scope
from hina_bot.discord.bot import (
    _bare_call_reply,
    _has_explicit_text_reply,
    _has_strong_visual_context,
)


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


def test_only_authorized_explicit_text_reply_bypasses_bare_call():
    reply = {"context_kind": "replied_message", "role": "user",
             "author_user_id": "100", "content": "답장 대상"}
    explicit = {"trigger_kind": "explicit"}

    def has(interaction, rows, policy="full"):
        return _has_explicit_text_reply(
            interaction, rows, current_user_id=100, policy=policy,
        )

    assert has(explicit, [reply])
    assert not has({"trigger_kind": "implicit"}, [reply])
    assert not has(explicit, [])
    assert not has(explicit, [{**reply, "content": ""}])
    assert not has(explicit, [{**reply, "context_kind": "recent_channel_message"}])
    assert not has(explicit, [{**reply, "author_user_id": "200"}], "bot_interactions_only")
    assert has(explicit, [reply], "bot_interactions_only")

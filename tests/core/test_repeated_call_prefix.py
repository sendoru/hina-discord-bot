from types import SimpleNamespace as NS

from hina_bot.core.routing import trigger_text


def _message(text: str):
    return NS(
        content=text,
        author=NS(bot=False),
        mentions=[],
        webhook_id=None,
        guild=NS(id=1),
        reference=None,
    )


def test_repeated_call_prefix_keeps_second_prefix_as_user_content():
    assert trigger_text(_message("히나야 히나야"), 99) == "히나야"
    assert trigger_text(_message("히나야, 히나야 왜 그래?"), 99) == "히나야 왜 그래?"

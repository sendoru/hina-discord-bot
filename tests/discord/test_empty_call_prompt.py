from hina_bot.routing import Scope

from hina_bot.discord.bot import _bare_call_reply
from hina_bot.discord.web_bot import _augment_empty_call


def test_bare_text_call_does_not_invent_llm_intent():
    assert _augment_empty_call("히나야", "", False) is None
    assert _augment_empty_call("<@99>", "", False) is None


def test_bare_visual_call_keeps_visual_prompt():
    assert _augment_empty_call("히나야", "", True) == "히나야 이 이미지나 스티커를 봐줘."


def test_nonempty_or_nontrigger_messages_are_unchanged():
    assert _augment_empty_call("히나야 뭐해", "뭐해", False) is None
    assert _augment_empty_call("그냥 채팅", None, False) is None


def test_bare_call_reply_is_neutral_except_special_dm():
    assert _bare_call_reply(Scope(1, 10, 100), 100) == "응? 무슨 일이야?"
    assert _bare_call_reply(Scope(None, 10, 101), 100) == "응? 무슨 일이야?"
    assert _bare_call_reply(Scope(None, 10, 100), 100) == "응, 선생님. 무슨 일이야?"

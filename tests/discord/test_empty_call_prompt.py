from hina_bot.discord.web_bot import _augment_empty_call


def test_bare_text_call_gets_persona_aware_prompt():
    assert _augment_empty_call("히나야", "", False) == "히나야 잠깐 봐줘."
    assert _augment_empty_call("<@99>", "", False) == "<@99> 잠깐 봐줘."


def test_bare_visual_call_keeps_visual_prompt():
    assert _augment_empty_call("히나야", "", True) == "히나야 이 이미지나 스티커를 봐줘."


def test_nonempty_or_nontrigger_messages_are_unchanged():
    assert _augment_empty_call("히나야 뭐해", "뭐해", False) is None
    assert _augment_empty_call("그냥 채팅", None, False) is None

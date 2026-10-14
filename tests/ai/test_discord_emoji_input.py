"""Discord emoji ID scrubbing is only for model-facing natural-language text."""

from hina_bot.ai.discord_emoji_input import (
    normalize_custom_emoji_text,
    normalized_message_context,
)


def test_normalize_emoji_name_without_exposing_snowflake():
    text = (
        "안녕 <:hina:123456789012345678> <:18:1495269226938630315> "
        "<a:wave:234567890123456789> <:smile:123456789012345678>"
    )
    assert normalize_custom_emoji_text(text) == (
        "안녕 :hina: :18: :wave: :smile:"
    )


def test_keep_non_emoji_discord_syntax_plain_alias_and_code_examples():
    text = (
        "<@123456789012345678> <#123456789012345678> :hina: "
        "<:broken:not-a-number> "
        "`<:raw:123456789012345678>` "
        "```text\n<a:code:123456789012345678>\n``` "
        "<:live:123456789012345678>"
    )
    assert normalize_custom_emoji_text(text) == (
        "<@123456789012345678> <#123456789012345678> :hina: "
        "<:broken:not-a-number> "
        "`<:raw:123456789012345678>` "
        "```text\n<a:code:123456789012345678>\n``` "
        ":live:"
    )


def test_context_normalization_preserves_identity_keys_and_raw_records():
    raw_emoji = "<:hello:123456789012345678>"
    context = {
        "active_reply_chain": [
            {"message_id": "99", "author_user_id": "14", "content": raw_emoji}
        ],
        "channel_recent_messages": [
            {"message_id": "101", "content": f"hello {raw_emoji}"}
        ],
        "personal_recent_conversation": [
            {"message_id": "102", "user": raw_emoji, "hina": f"응 {raw_emoji}"}
        ],
        "conversation_history": [
            {"role": "user", "content": raw_emoji}
        ],
        "public_server_context": [{"content": raw_emoji}],
        "current_interaction": {
            "mentions": [{"user_id": "123456789012345678"}],
        },
        "available_custom_emojis": [
            {"alias": ":hello:", "description": "greeting"}
        ],
        "structured_full_memory": [
            {"kind": "fact", "content": "Discord syntax " + raw_emoji}
        ],
    }

    projected = normalized_message_context(context)
    for section in (
        "active_reply_chain", "channel_recent_messages",
        "personal_recent_conversation", "conversation_history",
        "public_server_context",
    ):
        assert raw_emoji not in str(projected[section])
    assert projected["active_reply_chain"][0]["content"] == ":hello:"
    assert projected["active_reply_chain"][0]["message_id"] == "99"
    assert projected["personal_recent_conversation"][0]["hina"] == "응 :hello:"
    assert projected["current_interaction"] == context["current_interaction"]
    assert projected["structured_full_memory"] == context["structured_full_memory"]
    assert projected["available_custom_emojis"] == context["available_custom_emojis"]
    assert context["active_reply_chain"][0]["content"] == raw_emoji
    assert context["channel_recent_messages"][0]["content"] == f"hello {raw_emoji}"


def test_emoji_normalization_is_idempotent():
    value = "x <:18:1495269226938630315> y"
    once = normalize_custom_emoji_text(value)
    assert normalize_custom_emoji_text(once) == once

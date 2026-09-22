from types import SimpleNamespace as NS

from hina_bot.discord.interaction_context import build_interaction_context


def user(user_id, name, *, bot=False):
    return NS(id=user_id, display_name=name, name=name, bot=bot)


def test_mentions_and_reply_target_remain_distinct_structural_signals():
    hina = user(99, "히나", bot=True)
    rio = user(200, "리오", bot=True)
    speaker = user(100, "사용자")
    message = NS(
        author=speaker,
        mentions=[rio, hina],
    )
    replied = [{
        "author_user_id": "300",
        "name": "아리스",
        "role": "bot",
    }]

    context = build_interaction_context(message, 99, replied)

    assert context == {
        "speaker": {
            "user_id": "100",
            "name": "사용자",
            "is_bot": False,
            "is_self": False,
        },
        "mentions": [{
            "user_id": "200",
            "name": "리오",
            "is_bot": True,
            "is_self": False,
        }, {
            "user_id": "99",
            "name": "히나",
            "is_bot": True,
            "is_self": True,
        }],
        "reply_target": {
            "user_id": "300",
            "name": "아리스",
            "role": "bot",
            "is_self": False,
        },
    }
    assert "addressee" not in context
    assert "action_target" not in context


def test_duplicate_mentions_are_bounded_and_deduplicated():
    hina = user(99, "히나", bot=True)
    message = NS(author=user(100, "사용자"), mentions=[hina, hina])

    context = build_interaction_context(message, 99)

    assert len(context["mentions"]) == 1
    assert context["reply_target"] is None

def test_strict_metadata_can_omit_third_party_mention_names():
    hina = user(99, "히나", bot=True)
    rio = user(200, "리오", bot=True)
    message = NS(author=user(100, "사용자"), mentions=[rio, hina])

    context = build_interaction_context(
        message,
        99,
        include_mention_names=False,
    )

    assert context["mentions"][0]["user_id"] == "200"
    assert context["mentions"][0]["name"] == ""
    assert context["mentions"][1]["user_id"] == "99"
    assert context["mentions"][1]["name"] == "히나"


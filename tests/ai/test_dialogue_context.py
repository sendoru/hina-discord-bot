"""Regression tests for speaker-focused answer-only context projection."""

from copy import deepcopy

from hina_bot.ai.dialogue_context import project_dialogue_context


def _row(message_id, role, author, *, target=None, kind="channel_ambient"):
    row = {
        "message_id": str(message_id),
        "role": role,
        "author_user_id": str(author),
        "content": f"message-{message_id}",
        "context_kind": kind,
    }
    if target is not None:
        row["reply_target_user_id"] = str(target)
    return row


def test_new_speaker_is_not_assigned_previous_speakers_exchange():
    # A dozen turns between A and Hina before B arrives, with no B reply anchor.
    ambient = [
        _row(i, "user" if i % 2 == 0 else "assistant", 100 if i % 2 == 0 else 99,
             target=100 if i % 2 else None)
        for i in range(18)
    ]
    source = {"channel_recent_messages": ambient, "active_reply_chain": []}
    snapshot = deepcopy(source)

    provider, trace = project_dialogue_context(source)

    assert "channel_recent_messages" not in provider
    assert provider["speaker_recent_messages"] == []
    assert [row["message_id"] for row in provider["channel_ambient_messages"]] == [
        str(i) for i in range(10, 18)
    ]
    assert provider["conversation_focus"]["current_speaker_in_recent_channel"] is False
    assert trace["ambient_rows_input"] == 18
    assert trace["ambient_rows_sent"] == 8
    assert source == snapshot


def test_only_proven_same_speaker_or_reply_target_gets_direct_continuity():
    rows = [
        _row(1, "user", 100),
        _row(2, "assistant", 99, target=100),
        {**_row(3, "user", 200), "is_current_speaker": True},
        {**_row(4, "assistant", 99, target=200),
         "reply_target_is_current_speaker": True},
        _row(5, "assistant", 99),  # Unknown target after a restart: not B's reply.
    ]
    provider, trace = project_dialogue_context({"channel_recent_messages": rows})

    assert [r["message_id"] for r in provider["speaker_recent_messages"]] == ["3", "4"]
    assert [r["message_id"] for r in provider["channel_ambient_messages"]] == ["1", "2", "5"]
    assert trace["speaker_rows"] == 2
    assert provider["conversation_focus"]["current_speaker_in_recent_channel"] is True


def test_explicit_reply_keeps_wider_scene_and_causal_chain_untouched():
    ambient = [_row(i, "user", 100) for i in range(15)]
    chain = [_row(100, "user", 100, kind="replied_message")]
    source = {"channel_recent_messages": ambient, "active_reply_chain": chain}

    provider, trace = project_dialogue_context(source)

    assert len(provider["channel_ambient_messages"]) == 15
    assert provider["active_reply_chain"] is chain
    assert trace["has_active_reply"] is True


def test_target_history_keeps_independent_provenance_and_never_becomes_own_thread():
    target = _row(90, "user", 300, kind="target_user_history")
    own = _row(91, "user", 200, kind="speaker_thread")
    provider, _ = project_dialogue_context({
        "channel_recent_messages": [target, own],
    })

    assert provider["target_user_history"] == [target]
    assert provider["speaker_recent_messages"] == [own]
    assert provider["channel_ambient_messages"] == []


def test_existing_speaker_thread_does_not_truncate_ambient_scene():
    own = _row(1, "user", 200, kind="speaker_thread")
    ambient = [_row(i, "user", 100) for i in range(2, 16)]

    provider, trace = project_dialogue_context({
        "channel_recent_messages": [own, *ambient],
    })

    assert len(provider["channel_ambient_messages"]) == len(ambient)
    assert trace["ambient_rows_input"] == trace["ambient_rows_sent"]


def test_explicit_current_channel_scope_keeps_full_ambient_history():
    ambient = [_row(i, "user", 100) for i in range(15)]
    provider, trace = project_dialogue_context(
        {"channel_recent_messages": ambient}, preserve_ambient=True,
    )
    assert len(provider["channel_ambient_messages"]) == 15
    assert trace["ambient_rows_sent"] == 15

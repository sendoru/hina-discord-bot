from datetime import UTC, datetime, timedelta

from hina_bot.ai.structured_memory_context import recent_own_texts
from hina_bot.core.routing import Scope
from hina_bot.core.store import Store

DM = Scope(None, 10, 100)


def test_recent_own_texts_are_this_users_recent_turns_here():
    store = Store.create(":memory:")
    other = Scope(None, 10, 200)
    for message_id, text in enumerate(("첫 번째", "어제 고양이 병원 다녀왔어", "사료를 바꿨어"), 1):
        store.record_completed_turn(DM, message_id, text, "응", name="사용자")
    store.record_completed_turn(other, 9, "다른 사람 이야기", "응", name="다른 사람")
    now = datetime.now(UTC)

    assert recent_own_texts(store, DM, now) == ("어제 고양이 병원 다녀왔어", "사료를 바꿨어")
    assert recent_own_texts(store, DM, now + timedelta(hours=1)) == ()
    assert recent_own_texts(store, Scope(1, 10, 100), now) == ()
    store.close()


def test_recent_own_texts_skip_hina_replies_and_other_members_in_a_server():
    store = Store.create(":memory:")
    speaker, other = Scope(1, 20, 100, True), Scope(1, 20, 200, True)
    store.record_completed_turn(speaker, 1, "오늘 피곤하다", "고양이 보리는 잘 있어?", name="A")
    store.record_completed_turn(other, 2, "우리 고양이 귀엽지", "응", name="B")
    store.record_completed_turn(Scope(1, 21, 100, True), 3, "다른 채널 이야기", "응", name="A")

    # Only the speaker's own words in this channel: never Hina's reply or another member.
    assert recent_own_texts(store, speaker, datetime.now(UTC)) == ("오늘 피곤하다",)
    store.close()

from hina_bot.routing import Scope
from hina_bot.store import Store

from hina_bot.ai.contextual_routing import build_query, find_anchor, is_followup
from hina_bot.ai.routing_plan import RoutingPlan, build_routing_plan


def test_short_followup_reuses_same_speaker_topic():
    store = Store(":memory:")
    scope = Scope(1, 10, 100)
    rows = [{
        "role": "user",
        "user_id": "100",
        "author_user_id": "100",
        "content": "카요코가 예전에 뭐 했어?",
    }]
    try:
        assert is_followup("그럼 걔는 히나랑 만난 적 있어?")
        anchor = find_anchor(store, scope, rows, use_memory=True)
        query = build_query("그럼 걔는 히나랑 만난 적 있어?", anchor)
        assert "카요코" in query
        assert "히나랑 만난 적 있어?" in query
    finally:
        store.close()


def test_routing_plan_keeps_visible_turn_separate_from_expanded_query():
    store = Store(":memory:")
    scope = Scope(1, 10, 100)
    rows = [{
        "role": "user",
        "user_id": "100",
        "author_user_id": "100",
        "content": "서울 내일 날씨 어때?",
    }]
    try:
        plan = build_routing_plan(store, scope, "그럼 모레는?", rows)
        assert isinstance(plan, RoutingPlan)
        assert plan.visible_content == "그럼 모레는?"
        assert plan.expanded
        assert "서울" in plan.routing_query
        assert "모레" in plan.routing_query
    finally:
        store.close()


def test_other_speaker_is_not_implicit_anchor():
    store = Store(":memory:")
    scope = Scope(1, 10, 100)
    rows = [{
        "role": "user",
        "user_id": "200",
        "author_user_id": "200",
        "content": "서울 내일 날씨 어때?",
    }]
    try:
        assert find_anchor(store, scope, rows, use_memory=False) == ""
    finally:
        store.close()


def test_explicit_reply_outweighs_rolling_same_speaker_context():
    store = Store(":memory:")
    scope = Scope(1, 10, 100)
    rows = [
        {"role": "user", "user_id": "100", "content": "다른 주제"},
        {
            "role": "user",
            "user_id": "200",
            "content": "서울 내일 날씨 어때?",
            "context_kind": "replied_message",
        },
    ]
    try:
        anchor = find_anchor(store, scope, rows, use_memory=False)
        assert anchor == "서울 내일 날씨 어때?"
        assert "서울" in build_query("그럼 모레는?", anchor)
    finally:
        store.close()


def test_statement_does_not_inherit_previous_route():
    assert not is_followup("그럼 좋아")

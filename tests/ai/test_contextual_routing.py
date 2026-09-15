from hina_bot.ai.contextual_routing import (
    RoutingAnchor,
    build_query,
    find_anchor,
    find_prior_user_request,
    is_followup,
)
from hina_bot.ai.routing_plan import RoutingPlan, build_routing_plan
from hina_bot.core.routing import Scope
from hina_bot.core.store import Store


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
        query = build_query("그럼 걔는 히나랑 만난 적 있어?", anchor.text)
        assert anchor.source == "own_prior_turn"
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
        assert plan.anchor_source == "own_prior_turn"
        assert plan.prior_user_request == "서울 내일 날씨 어때?"
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
        assert find_anchor(store, scope, rows, use_memory=False) == RoutingAnchor()
        assert find_prior_user_request(store, scope, rows, use_memory=False) == ""
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
        assert anchor.text == "서울 내일 날씨 어때?"
        assert anchor.source == "explicit_reply"
        assert "서울" in build_query("그럼 모레는?", anchor.text)
    finally:
        store.close()


def test_statement_does_not_inherit_previous_route():
    assert not is_followup("그럼 좋아")


def test_explicit_bot_reply_and_prior_user_request_are_kept_separately():
    store = Store(":memory:")
    scope = Scope(1, 10, 100)
    rows = [
        {
            "role": "user",
            "author_user_id": "100",
            "content": "이 알고리즘의 시간 복잡도를 증명해 줘",
            "context_kind": "speaker_thread",
        },
        {
            "role": "assistant",
            "author_user_id": "999",
            "content": "귀납법으로 증명할 수 있어.",
            "context_kind": "replied_message",
        },
    ]
    try:
        plan = build_routing_plan(store, scope, "왜?", rows)
        assert plan.anchor == "귀납법으로 증명할 수 있어."
        assert plan.anchor_source == "explicit_reply"
        assert plan.prior_user_request == "이 알고리즘의 시간 복잡도를 증명해 줘"
    finally:
        store.close()


def test_conversation_history_is_an_owned_prior_request_source():
    store = Store(":memory:")
    scope = Scope(None, 10, 100)
    store.add(scope, 1, "이 오류의 원인을 찾아서 고쳐줘", "확인해볼게.")
    try:
        plan = build_routing_plan(store, scope, "왜?", [])
        assert plan.anchor_source == "conversation_history"
        assert plan.prior_user_request == "이 오류의 원인을 찾아서 고쳐줘"
    finally:
        store.close()


def test_external_explicit_reply_does_not_reuse_an_unrelated_complex_request():
    store = Store(":memory:")
    scope = Scope(1, 10, 100)
    rows = [
        {
            "role": "user",
            "author_user_id": "100",
            "content": "이 알고리즘의 시간 복잡도를 증명해 줘",
            "context_kind": "speaker_thread",
        },
        {
            "role": "user",
            "author_user_id": "200",
            "content": "이 코드를 분석해서 병목을 찾아야 한다",
            "context_kind": "replied_message",
        },
    ]
    try:
        plan = build_routing_plan(store, scope, "왜?", rows)
        assert plan.anchor_source == "explicit_reply"
        assert plan.prior_user_request == ""
    finally:
        store.close()

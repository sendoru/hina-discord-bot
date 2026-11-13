import asyncio
import json

import httpx
import pytest

from hina_bot.ai import decisions
from hina_bot.ai.decisions import DecisionsClient
from hina_bot.ai.prompts import load_prompt
from hina_bot.ai.retrieval_decisions import decision_query, pack_decision_lore
from hina_bot.core.knowledge_retrieval import KnowledgeCandidate


async def _evaluate(handler, *, candidates=None, key="test-key", domain="lore"):
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        return await DecisionsClient(http).evaluate(
            api_key=key, domain=domain, query={"current_message": "synthetic"},
            candidates={"lore.a": {"content": "first"}, "lore.b": {"content": "second"}}
            if candidates is None else candidates,
        )


def _answer(probability):
    return {"type": "noul", "noul": probability, "confidence": 0.99}


@pytest.mark.parametrize("domain", ["lore", "memory"])
async def test_rest_contract_and_question_id_mapping(domain):
    def handler(request):
        assert str(request.url) == decisions.URL
        assert request.headers["x-goog-api-key"] == "test-key"
        body = json.loads(request.content)
        assert set(body) == {"input", "questions", "model"}
        assert body["model"] == decisions.MODEL
        shared = json.loads(body["input"])
        rules = load_prompt(f"decisions_{domain}.md").strip()
        assert shared["selection_rules"] == rules
        assert request.content.count(rules.encode()) == 1
        assert shared["candidates"]["c1"]["content"] == "first"
        assert list(body["questions"]) == ["c1", "c2"]
        for key, question in body["questions"].items():
            assert question["type"] == "noul"
            assert f"candidate {key}" in question["instructions"]
            assert "selection_rules" in question["instructions"]
            assert len(question["instructions"]) < 100
        return httpx.Response(200, json={
            "answers": {"c2": _answer(0.99), "c1": _answer(0.01)},
            "usage": {"total_input_tokens": 123},
        })

    result = await _evaluate(handler, domain=domain)
    assert result.scores == {"lore.b": 0.99, "lore.a": 0.01}
    assert result.metrics()["decision_score_bands"] == {
        "below_half": 1, "below_threshold": 0, "eligible": 1,
    }
    assert result.input_tokens == 123 and result.question_count == 2


@pytest.mark.parametrize(("status", "body", "reason"), [
    (429, "private request echo", "rate_limited"),
    (503, "private request echo", "provider_error"),
    (400, "private request echo", "http_error"),
    (200, "not json", "invalid_json"),
    (200, "null", "invalid_response"),
    (200, '{"answers": []}', "invalid_answers"),
    (200, '{"answers": {}}', "missing_answers"),
    (200, '{"answers": {"c1": {}, "c1": {}, "c2": {}}}', "duplicate_response_key"),
    (200, '{"answers": {"c1": {}, "c2": {}, "c3": {}}}', "unexpected_answers"),
    (200, json.dumps({"answers": {"c1": {"type": "choice"}, "c2": _answer(0.4)}}),
     "invalid_answer_type"),
    *((200, json.dumps({"answers": {"c1": _answer(value), "c2": _answer(0.4)}}),
       "invalid_probability") for value in (True, "0.5", -1, 2, float("nan"))),
])
async def test_invalid_api_responses_fail_as_a_whole(status, body, reason):
    result = await _evaluate(lambda _request: httpx.Response(status, text=body))
    assert result.scores is None
    assert result.reason == reason
    assert result.request_ms is not None


async def test_timeout_and_transport_fail_once(monkeypatch):
    monkeypatch.setattr(decisions, "TIMEOUT_SECONDS", 0.01)
    attempts = 0

    async def slow(_request):
        nonlocal attempts
        attempts += 1
        await asyncio.sleep(5)

    result = await _evaluate(slow)
    assert result.reason == "timeout" and attempts == 1

    def offline(request):
        raise httpx.ConnectError("sensitive provider text", request=request)

    assert (await _evaluate(offline)).reason == "transport_error"


async def test_full_pool_never_truncated_on_limits():
    def unexpected(_request):
        pytest.fail("request should have been skipped")

    assert (await _evaluate(unexpected, candidates={})).scores == {}
    assert (await _evaluate(unexpected, key="")).reason == "missing_api_key"
    result = await _evaluate(unexpected, candidates={
        str(i): {} for i in range(decisions.MAX_QUESTIONS + 1)
    })
    assert result.candidate_count == 1001 and result.reason == "question_limit"

    result = await _evaluate(unexpected, candidates={"one": {"content": "한" * 100_000}})
    assert result.reason == "request_size_limit"
    assert result.request_bytes > decisions.MAX_REQUEST_BYTES and result.question_count == 1


async def test_question_limit_is_inclusive():

    def handler(request):
        questions = json.loads(request.content)["questions"]
        assert len(questions) == decisions.MAX_QUESTIONS
        return httpx.Response(200, json={"answers": {key: _answer(0.01) for key in questions}})

    result = await _evaluate(handler, candidates={str(i): {} for i in range(1000)})
    assert len(result.scores) == 1000


def test_query_filters_context_and_marks_speakers():
    rows = [
        {"context_kind": "channel_ambient", "role": "user", "user_id": "7",
         "content": "unrelated"},
        {"context_kind": "speaker_thread", "role": "user", "user_id": "7",
         "direct_trigger": True, "content": "own context"},
        {"context_kind": "speaker_thread", "role": "user", "user_id": "8",
         "direct_trigger": True, "content": "foreign context"},
        {"context_kind": "reply_origin_source", "role": "assistant",
         "provenance_class": "reference_derived", "content": "derived history"},
        {"context_kind": "reply_origin_request", "role": "user", "user_id": "8",
         "content": "foreign reply"},
        {"context_kind": "replied_message", "role": "assistant", "content": "causal reply"},
    ]
    def query(policy, *, owner_only=False):
        return decision_query(
            "<@123456789012345678> continue", rows, user_id=7, policy=policy,
            owner_only=owner_only, recent_own=("another own message",),
        )

    strict = query("bot_interactions_only")
    assert strict["current_message"] == "[Discord ID] continue"
    assert strict["recent_own"] == ["own context", "another own message"]
    assert [row["text"] for row in strict["explicit_reply"]] == ["causal reply"]
    assert query("full")["explicit_reply"][0]["speaker"] == "other_speaker"
    assert query("full", owner_only=True)["explicit_reply"] == strict["explicit_reply"]


def test_lore_packing_keeps_support_and_stable_ties_within_budget():
    def candidate(item_id, content, **options):
        return KnowledgeCandidate(item_id, "static_lore", "world_fact", content, "", (), (),
                                  **options)

    candidates = [
        candidate("c", "inference", fact_type="inference", evidence_ids=("a",)),
        candidate("b", "other fact"),
        candidate("a", "supporting fact"),
        candidate("dup", "supporting fact"),
        candidate("hidden_dependency", "unusable inference",
                  fact_type="inference", evidence_ids=("missing",)),
    ]
    rows = {c.candidate_id: c.content for c in candidates}
    result = decisions.DecisionResult(scores={
        "a": 0.01, "b": 0.8, "c": 0.8, "dup": 0.7, "hidden_dependency": 0.99,
    })
    packed, ids = pack_decision_lore(candidates, rows, result, max_items=3, max_chars=1000)
    assert ids == ["b", "c", "a"] and packed.items == 3
    packed, ids = pack_decision_lore(candidates, rows, result, max_items=1, max_chars=1000)
    assert ids == ["b"] and packed.items == 1
    assert pack_decision_lore(candidates, rows, result, max_items=3, max_chars=5)[1] == []

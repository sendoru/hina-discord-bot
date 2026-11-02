"""Memory response eval (#457): case setup, scoring and the runner without paid calls."""

import json
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace as NS
from zoneinfo import ZoneInfo

import httpx
import pytest
from openai import AsyncOpenAI

from hina_bot.ai.runtime_llm import LLM
from hina_bot.core.config import Settings
from hina_bot.core.memory_selection import effective_status
from hina_bot.core.store import Store
from hina_bot.tooling.memory_response_eval import (
    DEFAULT_CASES,
    JUDGE_INSTRUCTIONS,
    METRICS,
    _parser,
    _settings,
    first_turn_placements,
    keyword_hit,
    load_cases,
    parse_verdicts,
    run,
    seed_memories,
    summarize,
    write_report,
)

ROOT = Path(__file__).resolve().parents[2]
NOW = datetime(2026, 10, 30, 19, 0, tzinfo=ZoneInfo("Asia/Seoul"))
CAT = {"ref": "cat", "content": "고양이 '보리'를 키움",
       "annotation": {"importance": 3, "observed_days_ago": 60, "keywords": ["고양이", "보리"]}}


def _write(tmp_path, cases):
    path = tmp_path / "cases.jsonl"
    path.write_text("".join(json.dumps(case, ensure_ascii=False) + "\n" for case in cases),
                    encoding="utf-8")
    return path


def _case(**overrides):
    return {"id": "case", "covers": "test", "space": "dm", "memories": [CAT],
            "turns": ["오늘 날씨 좋다"], "checks": [{"type": "not_raised", "ref": "cat"}],
            **overrides}


@pytest.mark.parametrize("now", [NOW, None], ids=["fixed", "today"])
def test_repository_cases_set_up_what_they_claim(now):
    cases = load_cases(ROOT / DEFAULT_CASES)
    for case in cases:
        refs = {memory["ref"] for memory in case["memories"]}
        # Every memory's first-turn tiered placement is pinned, so a selection change
        # that stops a case from testing its claim fails here.
        assert set(case["tiered_expect"]) == refs, case["id"]
        assert first_turn_placements(case, now=now) == case["tiered_expect"], case["id"]
        store = Store.create(":memory:")
        moment = now or datetime.now(ZoneInfo("Asia/Seoul"))
        try:
            seeded = seed_memories(store, case, moment)
            for check in case["checks"]:
                if check["type"] == "status":
                    annotation = store.memory_annotation(seeded[check["ref"]]["id"])
                    assert effective_status(annotation, moment).status.value == check["status"]
        finally:
            store.close()


def test_repository_cases_cover_every_metric_space_and_time_state():
    cases = load_cases(ROOT / DEFAULT_CASES)
    checks = [check for case in cases for check in case["checks"]]
    assert {check["type"] for check in checks} == set(METRICS)
    assert {case["space"] for case in cases} == {"dm", "server"}
    assert {check["status"] for check in checks if check["type"] == "status"} >= {
        "just_passed", "unverified", "past",
    }
    placements = {value for case in cases for value in case["tiered_expect"].values()}
    assert placements >= {"profile", "quiet_boundaries", "directives", "notes:T1", "notes:T2"}
    assert any(len(case["turns"]) > 1 for case in cases)


def test_event_dates_are_relative_and_reach_the_legacy_content():
    case = _case(memories=[{"ref": "exam", "content": "{event} 시험 예정",
                            "annotation": {"time_scope": "planned", "event_in_days": -3}}],
                 checks=[{"type": "status", "ref": "exam", "status": "just_passed"}])
    store = Store.create(":memory:")
    try:
        seeded = seed_memories(store, case, NOW)
        assert seeded["exam"]["content"] == "10월 27일 시험 예정"
        assert store.memory_annotation(seeded["exam"]["id"]).event_at == "2026-10-27"
    finally:
        store.close()


@pytest.mark.parametrize("change,message", [
    ({"unknown": 1}, "case 키"),
    ({"space": "group"}, "space"),
    ({"turns": []}, "turns"),
    ({"checks": [{"type": "not_raised", "ref": "dog"}]}, "check type 또는 ref"),
    ({"checks": [{"type": "status", "ref": "cat"}]}, "status check"),
    ({"checks": [{"type": "not_raised", "ref": "cat", "status": "past"}]}, "status check"),
    ({"checks": [{"type": "not_raised", "ref": "cat", "turn": 2}]}, "turn"),
    ({"memories": [dict(CAT, annotation={"recurrence": "weekly:sat"})]}, "recurrence"),
    ({"memories": [dict(CAT, annotation={"usage_mode": "loud"})]}, "usage_mode"),
    ({"memories": [dict(CAT, kind="relationship")]}, "관계 기억"),
    ({"tiered_expect": {"cat": "notes:T9"}}, "tiered_expect"),
])
def test_loader_rejects_cases_that_cannot_measure_anything(tmp_path, change, message):
    with pytest.raises(ValueError, match=message):
        load_cases(_write(tmp_path, [_case(**change)]))


def test_loader_rejects_duplicate_and_unknown_ids(tmp_path):
    with pytest.raises(ValueError, match="중복"):
        load_cases(_write(tmp_path, [_case(), _case()]))
    with pytest.raises(ValueError, match="없는 case id"):
        load_cases(_write(tmp_path, [_case()]), ids=("missing",))


def test_parse_verdicts_accepts_fenced_json_and_drops_invalid_rows():
    checks = [{"id": "c1", "type": "not_raised"}, {"id": "c2", "type": "status"},
              {"id": "c3", "type": "boundary"}]
    text = '```json\n{"checks": [{"id": "c1", "verdict": "fail", "evidence": "보리 잘 있어?"},' \
           '{"id": "c2", "verdict": "na"}, {"id": "c3", "verdict": "na"},' \
           '{"id": "c9", "verdict": "pass"}]}\n```'

    verdicts = parse_verdicts(text, checks)

    assert verdicts == {"c1": {"verdict": "fail", "evidence": "보리 잘 있어?"},
                        "c2": {"verdict": "na", "evidence": ""}}
    assert parse_verdicts("not json", checks) == {}


def test_keyword_proxy_ignores_words_the_user_said():
    assert keyword_hit(["고양이", "보리"], "보리는 잘 있어?", ["오늘 날씨 좋다"])
    assert not keyword_hit(["고양이"], "고양이 귀엽지", ["우리 고양이 봐"])
    assert not keyword_hit([], "보리", ["안녕"])


def _client(calls, *, reply="응 보리도 잘 지내지?", judge=None):
    def handler(request):
        body = json.loads(request.content)
        calls.append(body)
        if body.get("instructions") == JUDGE_INSTRUCTIONS:
            payload = json.loads(body["input"])
            text = judge(payload) if judge else json.dumps({"checks": [
                {"id": check["id"],
                 "verdict": "fail" if "보리" in payload["reply"] else "pass",
                 "evidence": ""}
                for check in payload["checks"]
            ]})
        else:
            text = reply
        return httpx.Response(200, json={
            "id": "resp", "object": "response", "created_at": 0, "status": "completed",
            "model": body["model"],
            "output": [{"type": "message", "id": "m", "role": "assistant", "status": "completed",
                        "content": [{"type": "output_text", "text": text, "annotations": []}]}],
        })

    return AsyncOpenAI(api_key="test-not-a-real-key",
                       http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))


def _args(path, **overrides):
    return NS(**{"cases": path, "id": None, "limit": 0, "modes": ("legacy", "tiered"),
                 "repeat": 1, "no_judge": False, "judge_model": "judge-model",
                 "admin_database": None, **overrides})


def _factory(calls, **client_options):
    def make_llm(mode):
        return LLM(Settings(discord_token="test", openai_api_key="test",
                            personal_memory_read_mode=mode, chat_web_search=False),
                   client=_client(calls, **client_options))

    return make_llm


@pytest.mark.asyncio
async def test_runner_compares_read_modes_through_the_runtime_answer_path(tmp_path):
    calls = []
    path = _write(tmp_path, [_case(turns=["오늘 날씨 좋다", "심심하다"])])

    results, summary = await run(_args(path), make_llm=_factory(calls))

    legacy, tiered = sorted(results, key=lambda row: row["mode"])
    assert (legacy["mode"], tiered["mode"]) == ("legacy", "tiered")
    assert not legacy["error"] and not tiered["error"]
    assert legacy["turns"][0]["selection"] is None
    assert tiered["turns"][0]["selection"] == [
        {"ref": "cat", "tier": "Tp", "placement": "profile", "status": "current"}
    ]
    # The bot's post-reply bookkeeping ran: the reply named the cat, so it was used.
    assert tiered["turns"][0]["used"] == ["cat"]
    check = tiered["turns"][1]["checks"][0]
    assert (check["type"], check["ref"], check["verdict"], check["keyword_hit"]) == (
        "not_raised", "cat", "fail", True,
    )
    for mode in ("legacy", "tiered"):
        entry = summary["modes"][mode]["proactive_mention"]
        assert (entry["checks"], entry["judged"], entry["rate"]) == (2, 2, 1.0)
        assert entry["keyword_proxy_rate"] == 1.0

    answers = [body for body in calls if body.get("instructions") != JUDGE_INSTRUCTIONS]
    judges = [body for body in calls if body.get("instructions") == JUDGE_INSTRUCTIONS]
    assert len(answers) == 4 and len(judges) == 4
    tools = [{tool.get("name") for tool in body.get("tools", [])} for body in answers]
    # recall_memory is offered only on the tiered path, as in the bot.
    assert "recall_memory" not in tools[0] | tools[1]
    assert "recall_memory" in tools[2] and "recall_memory" in tools[3]
    judged = json.loads(judges[-1]["input"])
    assert judges[-1]["model"] == "judge-model" and judges[-1]["store"] is False
    assert judged["user_message"] == "심심하다"
    assert judged["earlier_turns"] == [{"user": "오늘 날씨 좋다", "hina": "응 보리도 잘 지내지?"}]
    assert judged["checks"] == [
        {"id": "c1", "type": "not_raised", "memory": "고양이 '보리'를 키움"}
    ]

    report = tmp_path / "report.md"
    write_report(report, summary, results)
    text = report.read_text(encoding="utf-8")
    assert "| tiered | proactive_mention | 2 / 2 | 1.0 |" in text
    assert "cat=Tp/profile" in text


@pytest.mark.asyncio
async def test_tiered_usage_is_stored_so_later_turns_see_the_cooldown(tmp_path):
    food = {"ref": "food", "kind": "preference", "content": "매운 음식을 좋아함",
            "annotation": {"keywords": ["음식", "매운", "배고픔"]}}
    case = _case(memories=[food], turns=["아 배고프다", "아 배고프다"],
                 checks=[{"type": "not_raised", "ref": "food", "turn": 2}])

    results, _summary = await run(_args(_write(tmp_path, [case]), modes=("tiered",)),
                                  make_llm=_factory([], reply="매운 거 먹자!"))

    first, second = results[0]["turns"]
    assert [item["ref"] for item in first["selection"]] == ["food"]
    assert first["used"] == ["food"]
    # Used a moment ago and only weakly related again: the 72-hour cooldown holds it back.
    assert second["selection"] == []


@pytest.mark.asyncio
async def test_checks_follow_their_turn_and_status_na_is_not_judged(tmp_path):
    exam = {"ref": "exam", "content": "{event} 시험 예정",
            "annotation": {"time_scope": "planned", "event_in_days": -3, "importance": 3,
                           "keywords": ["시험"]}}
    case = _case(memories=[exam], turns=["히나야 안녕", "저녁 뭐 먹지"], checks=[
        {"type": "status", "ref": "exam", "status": "just_passed", "turn": 1},
        {"type": "not_raised", "ref": "exam", "turn": 2},
    ])

    def judge(payload):
        verdict = "na" if payload["checks"][0]["type"] == "status" else "pass"
        return json.dumps({"checks": [{"id": payload["checks"][0]["id"], "verdict": verdict}]})

    results, summary = await run(
        _args(_write(tmp_path, [case]), modes=("tiered",)),
        make_llm=_factory([], reply="안녕!", judge=judge),
    )

    [row] = results
    assert [[check["type"] for check in turn["checks"]] for turn in row["turns"]] == [
        ["status"], ["not_raised"],
    ]
    assert row["turns"][0]["selection"][0]["tier"] == "T1"
    status = summary["modes"]["tiered"]["status_misstatement"]
    assert (status["judged"], status["na"], status["rate"], status["referenced_rate"]) == (
        0, 1, None, 0.0,
    )
    assert status["by_status"] == {"just_passed": {"judged": 0, "fail": 0, "na": 1}}


@pytest.mark.asyncio
async def test_without_a_judge_or_with_a_broken_one_the_run_still_reports(tmp_path):
    path = _write(tmp_path, [_case()])

    results, summary = await run(
        _args(path, modes=("tiered",)),
        make_llm=_factory([], judge=lambda payload: "I cannot grade this"),
    )
    entry = summary["modes"]["tiered"]["proactive_mention"]
    assert (entry["errors"], entry["judged"]) == (1, 0)
    assert results[0]["turns"][0]["checks"][0]["evidence"] == "judge_invalid_json"

    calls = []
    args = _args(path, modes=("tiered",))
    args.no_judge = True
    results, summary = await run(args, make_llm=_factory(calls))
    assert results[0]["turns"][0]["checks"][0]["verdict"] == "not_run"
    assert summary["modes"]["tiered"]["proactive_mention"]["keyword_proxy_rate"] == 1.0
    assert all(body.get("instructions") != JUDGE_INSTRUCTIONS for body in calls)


def test_cli_settings_pin_the_read_mode_and_turn_off_web_search(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-not-a-real-key")
    monkeypatch.setenv("CHAT_WEB_SEARCH", "true")
    monkeypatch.setenv("MEMORY_SESSION_FOLLOWUP", "off")
    args = _parser().parse_args(["--provider", "openai", "--model", "m", "--modes", "tiered"])

    settings = _settings(args, "tiered")

    assert args.modes == ("tiered",)
    assert (settings.personal_memory_read_mode, settings.chat_web_search) == ("tiered", False)
    assert settings.memory_session_followup == "off"
    with pytest.raises(SystemExit):
        _parser().parse_args(["--modes", "shadow"])
    monkeypatch.setenv("MEMORY_SESSION_FOLLOWUP", "sometimes")
    with pytest.raises(ValueError, match="MEMORY_SESSION_FOLLOWUP"):
        _settings(args, "legacy")


def test_summary_separates_modes_and_ignores_errors_in_rates():
    def row(mode, verdicts):
        return {"id": "a", "mode": mode, "error": "", "turns": [{"checks": [
            {"type": "boundary", "verdict": verdict, "keyword_hit": False}
            for verdict in verdicts
        ]}]}

    summary = summarize([row("legacy", ["fail", "pass"]), row("tiered", ["pass", "error"])])

    assert summary["modes"]["legacy"]["boundary_leak"]["rate"] == 0.5
    tiered = summary["modes"]["tiered"]["boundary_leak"]
    assert (tiered["judged"], tiered["errors"], tiered["rate"]) == (1, 1, 0.0)

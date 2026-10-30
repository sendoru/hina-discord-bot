"""Personal extraction eval runner (#451): inputs, scoring and reporting without API calls."""

import json
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock

import pytest

from hina_bot.ai.memory_summary import MemorySummaryMixin
from hina_bot.tooling.memory_extraction_eval import (
    DEFAULT_CASES,
    case_inputs,
    load_cases,
    run,
)

ROOT = Path(__file__).resolve().parents[2]


class Worker(MemorySummaryMixin):
    def __init__(self, *responses):
        self.settings = NS(model="m", model_routing_mode="fixed", memory_routing_smart_threshold=2.0,
                           memory_output_tokens=1024, provider="openai", runtime_timezone="Asia/Seoul",
                           gemini_thinking_level="low")
        self.client = object()
        self.usage = NS(request=AsyncMock(side_effect=responses), routing_event=lambda *a, **k: None)


def _response(items):
    return NS(status="completed", output_text=json.dumps({"items": items}, ensure_ascii=False))


def _args(*ids, repeat=1):
    return NS(cases=ROOT / DEFAULT_CASES, id=list(ids), limit=0, repeat=repeat)


def test_only_personal_cases_load_and_inputs_match_runtime_shape():
    cases = load_cases(ROOT / DEFAULT_CASES)
    assert cases and all(case["target"] == "personal" for case in cases)

    (case,) = load_cases(ROOT / DEFAULT_CASES, ids=("extract_job_correction",))
    payload, allowed, source_turns, refs, public = case_inputs(case, "Asia/Seoul")

    assert allowed == {"m1"} and refs == {"job_a": 1} and not public
    assert payload["turns"][0]["at_local"] == "2026-10-30T20:55+09:00"
    assert payload["turns"][0]["weekday"] == "FRI"
    assert payload["existing_memory_candidates"][0]["slot"] == "직장"
    assert source_turns["m1"]["user"].startswith("아 나 지난달에")


@pytest.mark.asyncio
async def test_relative_date_case_passes_and_wrong_scope_fails():
    good = {"content": "2026-11-03 면접 예정", "kind": "event", "disclosure": "local",
            "confidence": 0.9, "source_message_ids": ["m1"], "evidence": "다음 주 화요일에 면접",
            "time_scope": "planned", "event_at": "2026-11-03", "usage_mode": "ambient",
            "importance": 3}
    worker = Worker(_response([good]), _response([{**good, "event_at": "2026-11-10"}]))

    results, summary = await run(_args("extract_relative_date_resolved", repeat=2), worker)

    assert [row["status"] for row in results] == ["passed", "failed"]
    assert "raw_output" not in results[0]
    assert '"2026-11-10"' in results[1]["raw_output"]
    assert any("event_at" in failure for failure in results[1]["failures"])
    assert results[0]["items"][0]["event_at"] == "2026-11-03"
    assert summary["per_case_pass_rate"] == {"extract_relative_date_resolved": 0.5}


@pytest.mark.asyncio
async def test_forbidden_reading_and_correction_relation_are_scored():
    past_as_current = {"content": "피아노를 침", "kind": "fact", "disclosure": "local",
                       "confidence": 0.9, "source_message_ids": ["m1"],
                       "evidence": "중학교 때 피아노 쳤었는데", "time_scope": "ongoing",
                       "usage_mode": "contextual"}
    correction = {"content": "회사 B에 다님", "kind": "fact", "disclosure": "local",
                  "confidence": 0.9, "source_message_ids": ["m1"], "evidence": "회사 B로 이직했어",
                  "time_scope": "ongoing", "stability": "long", "usage_mode": "ambient",
                  "slot": "직장", "relation": {"type": "corrects", "target_item_id": 1,
                                              "confidence": 0.9}}
    worker = Worker(_response([past_as_current]), _response([correction]))

    results, _ = await run(
        _args("extract_past_experience_not_current", "extract_job_correction"), worker
    )

    statuses = {row["id"]: row for row in results}
    assert statuses["extract_past_experience_not_current"]["status"] == "failed"
    assert any(failure.startswith("forbid_items[0]")
               for failure in statuses["extract_past_experience_not_current"]["failures"])
    assert statuses["extract_job_correction"]["status"] == "passed", (
        statuses["extract_job_correction"]["failures"]
    )


@pytest.mark.asyncio
async def test_rejected_rows_keep_the_raw_answer_for_diagnosis():
    rejected = {"content": "회사 얘기를 먼저 꺼내지 않기", "kind": "directive", "disclosure": "local",
                "confidence": 0.9, "source_message_ids": ["m1"]}
    worker = Worker(_response([rejected]))

    (row,), _ = await run(_args("extract_boundary_single_statement"), worker)

    assert row["status"] == "failed" and row["rejected_items"] == 1 and row["items"] == []
    assert '"kind": "directive"' in row["raw_output"]

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
    load_case_images,
    load_cases,
    run,
    summarize,
)

ROOT = Path(__file__).resolve().parents[2]
PNG = b"\x89PNG\r\n\x1a\n" + b"fixture"


class Worker(MemorySummaryMixin):
    def __init__(self, *responses):
        self.settings = NS(model="m", model_routing_mode="fixed", memory_routing_smart_threshold=2.0,
                           memory_output_tokens=1024, provider="openai", runtime_timezone="Asia/Seoul",
                           gemini_thinking_level="low")
        self.client = object()
        self.usage = NS(request=AsyncMock(side_effect=responses), routing_event=lambda *a, **k: None)


def _response(items):
    return NS(status="completed", output_text=json.dumps({"items": items}, ensure_ascii=False))


def _args(*ids, repeat=1, image_dir=None):
    return NS(cases=ROOT / DEFAULT_CASES, id=list(ids), limit=0, repeat=repeat,
              image_dir=image_dir)


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
    rejected = {"content": "회사 얘기를 먼저 꺼내지 않기", "kind": "boundary", "disclosure": "local",
                "confidence": 0.9, "source_message_ids": ["m9"]}
    worker = Worker(_response([rejected]))

    (row,), _ = await run(_args("extract_boundary_single_statement"), worker)

    assert row["status"] == "failed" and row["rejected_items"] == 1 and row["items"] == []
    assert '"m9"' in row["raw_output"]


def _grounded(**overrides):
    item = {"content": "강아지(사진상 말티즈로 보임)를 키움", "kind": "fact", "disclosure": "local",
            "confidence": 0.95, "source_message_ids": ["m1"], "evidence": "우리 애 오늘 미용하고 왔어",
            "time_scope": "ongoing", "stability": "long", "usage_mode": "ambient",
            "visual_refs": ["m1"]}
    item.update(overrides)
    return item


def test_image_cases_name_bare_files_that_load_from_the_image_directory(tmp_path):
    (case,) = load_cases(ROOT / DEFAULT_CASES, ids=("extract_visual_pet_dog_species_added",))
    assert case["turns"][0]["images"] == ["pet_dog.jpg"]

    visuals, missing = load_case_images(case, tmp_path)
    assert visuals == [] and missing == ["pet_dog.jpg"]

    (tmp_path / "pet_dog.jpg").write_bytes(PNG)
    visuals, missing = load_case_images(case, tmp_path)
    assert missing == [] and [(v.message_id, v.index, v.mime_type) for v in visuals] == [
        ("m1", 1, "image/png")
    ]


def test_images_cannot_be_read_from_outside_the_image_directory(tmp_path):
    (tmp_path / "secret.png").write_bytes(PNG)
    case = {"turns": [{"images": ["../secret.png", "sub/../secret.png", "notes.txt"]}]}
    (tmp_path / "images").mkdir()
    (tmp_path / "images" / "notes.txt").write_bytes(b"not an image")

    visuals, missing = load_case_images(case, tmp_path / "images")

    assert visuals == []
    assert missing == ["../secret.png", "sub/../secret.png", "notes.txt"]


@pytest.mark.asyncio
async def test_image_case_without_its_photos_is_skipped_not_failed():
    worker = Worker()

    (row,), summary = await run(_args("extract_visual_pet_dog_species_added"), worker)

    assert row["status"] == "skipped" and row["missing_images"] == ["pet_dog.jpg"]
    worker.usage.request.assert_not_awaited()
    assert summary["status"] == {"skipped": 1}
    assert summary["pass_rate"] is None
    assert summary["per_case_pass_rate"] == {"extract_visual_pet_dog_species_added": None}


@pytest.mark.asyncio
async def test_image_case_sends_the_photo_and_scores_visual_constraints(tmp_path):
    (tmp_path / "pet_dog.jpg").write_bytes(PNG)
    worker = Worker(_response([_grounded()]), _response([_grounded(visual_refs=None)]))

    results, summary = await run(
        _args("extract_visual_pet_dog_species_added", repeat=2, image_dir=tmp_path), worker
    )

    first_call = worker.usage.request.await_args_list[0].kwargs
    (message,) = first_call["input"]
    assert [b["type"] for b in message["content"]] == ["input_text", "input_text", "input_image"]
    assert [row["status"] for row in results] == ["passed", "failed"]
    assert results[0]["items"][0]["confidence"] == 0.75
    assert "visual_grounded" in results[0]["items"][0]["lint_flags"]
    assert any("visual_grounded" in failure for failure in results[1]["failures"])
    assert summary["per_case_pass_rate"] == {"extract_visual_pet_dog_species_added": 0.5}


@pytest.mark.asyncio
async def test_photo_only_item_is_rejected_so_the_no_items_case_passes(tmp_path):
    (tmp_path / "pet_dog.jpg").write_bytes(PNG)
    photo_only = _grounded(source_message_ids=["m1"], evidence="사진 속 강아지")
    worker = Worker(_response([photo_only]))

    (row,), _ = await run(
        _args("extract_visual_reaction_only_no_items", image_dir=tmp_path), worker
    )

    assert row["status"] == "passed" and row["rejected_items"] == 1 and row["items"] == []


def test_summary_pass_rate_ignores_skipped_runs():
    summary = summarize([
        {"id": "a", "status": "passed", "failures": []},
        {"id": "a", "status": "failed", "failures": ["x"]},
        {"id": "b", "status": "skipped", "failures": []},
    ])

    assert summary["pass_rate"] == 0.5
    assert summary["per_case_pass_rate"] == {"a": 0.5, "b": None}

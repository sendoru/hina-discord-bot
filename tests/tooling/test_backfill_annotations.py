"""Offline annotation backfill (#451): targets, safety and lifecycle preservation."""

import json
from dataclasses import replace
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock

import pytest

from hina_bot.ai.memory_extraction import parse_annotation
from hina_bot.ai.memory_summary import MemorySummaryMixin
from hina_bot.core.memory_annotations import (
    LINT_DIRECTIVE_UNVERIFIED,
    LINT_EVIDENCE_MISMATCH,
    Annotator,
    UsageMode,
)
from hina_bot.core.memory_items import MemoryKind
from hina_bot.core.routing import Scope
from hina_bot.core.scope_overrides import scope_target_key
from hina_bot.core.store import Store
from hina_bot.tooling import backfill_annotations
from hina_bot.tooling.backfill_annotations import backfill, main, plan_backfill

DM = Scope(None, 10, 100)
MUTED = Scope(1, 20, 100, True)


class Worker(MemorySummaryMixin):
    def __init__(self, *responses):
        self.settings = NS(model="m", model_routing_mode="fixed", memory_routing_smart_threshold=2.0,
                           memory_output_tokens=1024, provider="openai", runtime_timezone="Asia/Seoul",
                           gemini_thinking_level="low")
        self.client = object()
        self.usage = NS(request=AsyncMock(side_effect=responses), routing_event=lambda *a, **k: None)

    def payloads(self):
        return [json.loads(call.kwargs["input"]) for call in self.usage.request.await_args_list]


def _response(rows):
    return NS(status="completed", output_text=json.dumps({"items": rows}, ensure_ascii=False))


def _seed(store):
    store.add(DM, 501, "나 고양이 보리 키워", "reply")
    ids = {
        "sourced": store.add_memory_item(DM, "고양이 보리를 키움", kind="fact", disclosure="local",
                                         source_message_ids=("501",), confidence=0.9),
        "boundary": store.add_memory_item(DM, "회사 얘기를 먼저 꺼내지 않기를 원함", kind="boundary",
                                          disclosure="local", source_message_ids=("400",),
                                          confidence=0.9),
        "stale": store.add_memory_item(DM, "커피를 못 마심", kind="preference", disclosure="local",
                                       confidence=0.9),
        "admin": store.add_memory_item(DM, "관리자 검토 기억", kind="fact", disclosure="local",
                                       confidence=0.9),
        "muted": store.add_memory_item(MUTED, "끈 채널의 기억", kind="fact", disclosure="local",
                                       confidence=0.9),
        "relationship": store.add_memory_item(DM, "편하게 대화함", kind="relationship",
                                              disclosure="implicit", confidence=0.9),
    }
    cited = [{"user": "커피 못 마셔", "at": "2026-10-01 00:00:00"}]
    old = replace(parse_annotation({"evidence": "커피 못 마셔", "time_scope": "ongoing",
                                    "usage_mode": "ambient"}, MemoryKind.PREFERENCE, cited),
                  memory_item_id=ids["stale"])
    store.upsert_memory_annotation(old)
    store.reconfirm_memory_annotation(ids["stale"], "2026-10-15 00:00:00")
    store.upsert_memory_annotation(replace(old, memory_item_id=ids["admin"], annotator=Annotator.ADMIN))
    for name in ("stale", "admin"):
        store.revise_memory_item(ids[name], expected_revision=0, content=f"{name} edited",
                                 kind="preference", disclosure="local", confidence=0.9)
    store.set_memory_mode_override(scope_target_key(MUTED, "channel"), "off")
    return ids


def test_plan_counts_missing_stale_and_admin_review_only():
    store = Store.create(":memory:")
    ids = _seed(store)

    plan = plan_backfill(store.db)

    assert plan.missing == (ids["sourced"], ids["boundary"], ids["muted"])
    assert plan.stale == (ids["stale"],)
    assert plan.stale_admin == 1
    store.close()


@pytest.mark.asyncio
async def test_backfill_annotates_with_sources_and_keeps_lifecycle():
    store = Store.create(":memory:")
    ids = _seed(store)
    worker = Worker(
        _response([
            {"index": 0, "evidence": "고양이 보리 키워", "time_scope": "ongoing", "stability": "long",
             "usage_mode": "ambient", "slot": "", "keywords": ["보리"]},
            {"index": 1, "evidence": "", "time_scope": "ongoing", "usage_mode": "directive",
             "directive_type": "boundary"},
            {"index": 2, "evidence": "", "time_scope": "ongoing", "usage_mode": "ambient"},
        ]),
    )

    annotated, disabled, changed, failed = await backfill(store, worker, plan_backfill(store.db))

    assert (annotated, disabled, changed, failed) == (3, 1, 0, {})
    (payload,) = worker.payloads()
    assert payload["items"][0]["source_turns"][0]["user"] == "나 고양이 보리 키워"
    assert "source_turns" not in payload["items"][1]  # its source turn is gone
    assert all("muted" not in entry["content"] for entry in payload["items"])

    sourced = store.memory_annotation(ids["sourced"])
    assert (sourced.annotator, sourced.usage_mode, sourced.evidence) == (
        Annotator.BACKFILL, UsageMode.AMBIENT, "고양이 보리 키워"
    )
    boundary = store.memory_annotation(ids["boundary"])
    assert boundary.usage_mode == UsageMode.DIRECTIVE and not boundary.verified
    assert LINT_DIRECTIVE_UNVERIFIED in boundary.lint_flags
    stale = store.memory_annotation(ids["stale"])
    assert stale.item_revision == 1 and stale.confirmations == 2
    assert stale.last_confirmed_at == "2026-10-15 00:00:00"
    assert stale.usage_mode == UsageMode.CONTEXTUAL  # unquoted ambient is demoted
    assert LINT_EVIDENCE_MISMATCH in stale.lint_flags
    assert store.memory_annotation(ids["admin"]).annotator == Annotator.ADMIN
    assert store.memory_annotation(ids["muted"]) is None
    assert store.memory_annotation(ids["relationship"]) is None
    after = plan_backfill(store.db)
    assert (after.missing, after.stale, after.stale_admin) == ((ids["muted"],), (), 1)
    store.close()


@pytest.mark.asyncio
async def test_failed_batch_writes_nothing():
    store = Store.create(":memory:")
    ids = _seed(store)
    worker = Worker(RuntimeError("provider down"))

    result = await backfill(store, worker, plan_backfill(store.db))

    assert result == (0, 1, 0, {"RuntimeError": 1})
    assert store.memory_annotation(ids["sourced"]) is None
    store.close()


def test_dry_run_and_missing_confirmation_never_write(tmp_path, monkeypatch, capsys):
    path = tmp_path / "hina.sqlite3"
    store = Store.create(str(path))
    _seed(store)
    store.close()
    called = []
    monkeypatch.setattr(backfill_annotations, "run_backfill", lambda *a, **k: called.append(a))

    assert main(["--database", str(path)]) == 0
    assert main(["--database", str(path), "--yes"]) == 2

    output = capsys.readouterr().out
    assert "missing=3 stale=1 stale_admin_review=1" in output
    assert "고양이" not in output
    assert called == []

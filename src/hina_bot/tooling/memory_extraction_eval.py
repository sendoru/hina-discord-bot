"""Live personal-memory extraction eval for memory v2 (#451).

Runs the ``target: personal`` cases of ``evals/memory_extraction_cases.jsonl`` through
the runtime's extractor input, parser and directive verification, then checks each
case's ``expect_items``/``forbid_items``/``expect_no_items`` constraints. These are
paid model calls and never part of pytest. Judgments are probabilistic, so read the
failed constraints and the extracted items, not only the pass rate.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from collections import Counter
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

from dotenv import load_dotenv

from hina_bot.ai.memory_extraction import (
    SHADOW_EXTRACTION_POLICY,
    build_reconciliation_candidates,
    build_shadow_turns,
    parse_shadow_extraction,
    shadow_extraction_payload,
)
from hina_bot.ai.memory_model_routing import build_memory_model_plan
from hina_bot.ai.memory_summary import MemorySummaryMixin
from hina_bot.ai.providers import create_provider_client, normalize_provider
from hina_bot.ai.usage import UsageLogger
from hina_bot.core.config import Settings
from hina_bot.core.memory_annotations import (
    Annotator,
    MemoryAnnotation,
    Stability,
    TimeScope,
    UsageMode,
    enforce_consistency,
    normalize_text,
)
from hina_bot.core.memory_items import MemoryDisclosure, MemoryKind

DEFAULT_CASES = Path("evals/memory_extraction_cases.jsonl")
DEFAULT_RESULTS = Path("data/evals")
# Fictional identifiers; eval payloads never carry real Discord ids.
SPEAKER_ID = "910001"
CHANNEL_ID = "920001"
GUILD_ID = "930001"
# Server-memory constraints are scored by the P4 runner.
SERVER_ONLY_KEYS = frozenset({"trust", "subject_users", "subject_users_include"})


def load_cases(path: Path, *, ids: tuple[str, ...] = ()) -> list[dict]:
    cases = [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    cases = [case for case in cases if case.get("target") == "personal"]
    if ids:
        cases = [case for case in cases if case["id"] in ids]
    return cases


def case_inputs(case: dict, zone: str):
    """Build the runtime-shaped extractor input for one case."""

    public = case["space"] == "public"
    pending = [
        {
            "message_id": f"m{index}",
            "created_at": datetime.fromisoformat(turn["at"]).astimezone(UTC).strftime(
                "%Y-%m-%d %H:%M:%S"
            ),
            "content": turn["user"],
            "reply": turn.get("hina", ""),
            "exportable": int(public),
        }
        for index, turn in enumerate(case["turns"], 1)
    ]
    turns, allowed, _context, _visibility = build_shadow_turns(
        pending, include_replies=True, zone_name=zone
    )
    refs: dict[str, int] = {}
    candidates = []
    annotations = {}
    for item_id, memory in enumerate(case.get("existing_memories", ()), 1):
        refs[memory["ref"]] = item_id
        candidates.append(SimpleNamespace(
            id=item_id,
            content=memory["content"],
            kind=MemoryKind(memory.get("kind", "fact")),
            disclosure=MemoryDisclosure(memory.get("disclosure", "local")),
            confidence=float(memory.get("confidence", 0.9)),
        ))
        raw = memory.get("annotation") or {}
        if raw:
            annotations[item_id] = MemoryAnnotation(
                memory_item_id=item_id,
                item_revision=0,
                time_scope=TimeScope(raw.get("time_scope", "unknown")),
                stability=Stability(raw["stability"]) if raw.get("stability") else None,
                event_at=raw.get("event_at"),
                slot=raw.get("slot", ""),
                observed_at="",
                last_confirmed_at="",
                usage_mode=UsageMode(raw.get("usage_mode", "contextual")),
                annotator=Annotator.EXTRACTOR,
                annotator_version="eval",
            )
    realm = f"dm:{SPEAKER_ID}" if case["space"] == "dm" else f"guild:{GUILD_ID}"
    payload = shadow_extraction_payload(
        speaker_id=SPEAKER_ID,
        speaker_name="",
        realm=realm,
        channel_id=CHANNEL_ID,
        turns=turns,
        candidates=build_reconciliation_candidates(candidates, annotations),
    )
    source_turns = {turn["message_id"]: turn for turn in turns}
    return payload, allowed, source_turns, refs, public


def _value(annotation, name):
    value = getattr(annotation, name, None)
    return value.value if hasattr(value, "value") else value


def constraint_failures(item, constraint: dict, refs: dict[str, int]) -> list[str]:
    """Names of the constraint keys this item does not satisfy."""

    content = normalize_text(item.content)
    annotation = item.annotation
    failures = []
    for key, expected in constraint.items():
        if key in SERVER_ONLY_KEYS:
            continue
        if key == "match_all":
            ok = all(normalize_text(word) in content for word in expected)
        elif key == "match_any":
            ok = any(normalize_text(word) in content for word in expected)
        elif key == "not_match_any":
            ok = not any(normalize_text(word) in content for word in expected)
        elif key == "relation":
            relation = item.relation
            ok = (
                relation is not None
                and relation.relation == expected["type"]
                and relation.target_item_id == refs.get(expected["target"])
            )
        elif key == "relation_absent":
            ok = (item.relation is None) is bool(expected)
        elif annotation is None:
            ok = False
        elif key == "importance_min":
            ok = annotation.importance >= int(expected)
        elif key == "recurrence_prefix":
            ok = (annotation.recurrence or "").startswith(expected)
        elif key.endswith("_in"):
            ok = _value(annotation, key.removesuffix("_in")) in expected
        else:
            ok = _value(annotation, key) == expected
        if not ok:
            failures.append(key)
    return failures


def score_case(case: dict, items, refs: dict[str, int]) -> list[str]:
    """Human-readable failures for one run; empty means the case passed."""

    failures = []
    if case.get("expect_no_items") and items:
        failures.append(f"expect_no_items: {len(items)} item(s) extracted")
    for index, constraint in enumerate(case.get("expect_items", ())):
        attempts = [constraint_failures(item, constraint, refs) for item in items]
        if not any(not failed for failed in attempts):
            closest = min(attempts, key=len, default=list(constraint))
            failures.append(f"expect_items[{index}] unmatched: {', '.join(closest)}")
    for index, constraint in enumerate(case.get("forbid_items", ())):
        if any(not constraint_failures(item, constraint, refs) for item in items):
            failures.append(f"forbid_items[{index}] matched")
    return failures


def _item_report(item) -> dict:
    annotation = item.annotation
    return {
        "content": item.content,
        "kind": item.kind.value,
        **({"relation": {"type": item.relation.relation, "target": item.relation.target_item_id}}
           if item.relation else {}),
        **({
            name: _value(annotation, name)
            for name in (
                "time_scope", "stability", "event_at", "recurrence", "usage_mode",
                "directive_type", "audience", "importance", "slot", "explicit", "verified",
                "evidence", "lint_flags",
            )
        } if annotation else {}),
    }


async def run_case(worker, case: dict) -> dict:
    zone = getattr(worker.settings, "runtime_timezone", "") or "Asia/Seoul"
    payload, allowed, source_turns, refs, public = case_inputs(case, zone)
    response = await worker._memory_request(
        "extract_memory_items_shadow",
        SHADOW_EXTRACTION_POLICY,
        payload,
        build_memory_model_plan(worker.settings, "", payload, shared=False),
    )
    if response.status != "completed" or not response.output_text.strip():
        return {"id": case["id"], "status": "error", "failures": ["empty extractor response"]}
    parsed = parse_shadow_extraction(
        response.output_text,
        allowed_source_ids=allowed,
        allowed_target_item_ids=set(refs.values()),
        source_turns=source_turns,
    )
    if not parsed.valid:
        return {"id": case["id"], "status": "invalid", "failures": ["invalid extractor JSON"],
                "raw_output": response.output_text}
    items, *_ = await worker._verify_memory_directives(parsed.items, source_turns)
    # Persistence narrows the audience by capture visibility; mirror it here.
    items = tuple(
        replace(item, annotation=enforce_consistency(item.annotation, public_origin=public))
        if item.annotation else item
        for item in items
    )
    failures = score_case(case, items, refs)
    row = {
        "id": case["id"],
        "status": "failed" if failures else "passed",
        "failures": failures,
        "rejected_items": parsed.rejected_items,
        "items": [_item_report(item) for item in items],
    }
    # Cases are fictional, so the raw answer can be kept to explain a failure or a
    # row the parser rejected (the report otherwise shows only accepted items).
    if failures or parsed.rejected_items or parsed.rejected_relations:
        row["raw_output"] = response.output_text
    return row


def summarize(results: list[dict]) -> dict:
    by_case: dict[str, Counter] = {}
    for row in results:
        by_case.setdefault(row["id"], Counter())[row["status"]] += 1
    statuses = Counter(row["status"] for row in results)
    return {
        "runs": len(results),
        "cases": len(by_case),
        "status": dict(statuses),
        "pass_rate": round(statuses["passed"] / len(results), 3) if results else None,
        "per_case_pass_rate": {
            case_id: round(counts["passed"] / sum(counts.values()), 3)
            for case_id, counts in sorted(by_case.items())
        },
    }


class _EvalWorker(MemorySummaryMixin):
    def __init__(self, settings):
        self.settings = settings
        self.client = create_provider_client(settings, settings.provider)
        self.usage = UsageLogger(settings.usage_log_path)

    async def close(self):
        try:
            await self.client.close()
        finally:
            self.usage.close()


def _settings(args) -> Settings:
    provider = normalize_provider(args.provider or os.getenv("LLM_PROVIDER", "openai"))
    model = args.model or os.getenv("LLM_MODEL", "").strip()
    if not model:
        raise SystemExit("--model 또는 LLM_MODEL이 필요합니다.")
    return Settings(
        discord_token="eval-only",
        provider=provider,
        openai_api_key=os.getenv("OPENAI_API_KEY", "").strip(),
        gemini_api_key=os.getenv("GEMINI_API_KEY", "").strip(),
        openrouter_api_key=os.getenv("OPENROUTER_API_KEY", "").strip(),
        model=model,
        model_routing_mode="fixed",
        memory_output_tokens=int(os.getenv("MEMORY_MAX_OUTPUT_TOKENS", "4096")),
        gemini_thinking_level=(
            args.gemini_thinking_level or os.getenv("GEMINI_THINKING_LEVEL", "low")
        ).strip().lower(),
        runtime_timezone=os.getenv("RUNTIME_TIMEZONE", "Asia/Seoul").strip() or "Asia/Seoul",
        usage_log_path=args.usage_log,
    )


async def run(args, worker=None) -> tuple[list[dict], dict]:
    cases = load_cases(args.cases, ids=tuple(args.id or ()))
    if args.limit:
        cases = cases[:args.limit]
    owned = worker is None
    worker = worker or _EvalWorker(_settings(args))
    results = []
    try:
        for _ in range(args.repeat):
            for case in cases:
                try:
                    row = await run_case(worker, case)
                except Exception as exc:  # noqa: BLE001 - one case must not stop the run
                    row = {"id": case["id"], "status": "error", "failures": [type(exc).__name__]}
                results.append(row)
                detail = "; ".join(row["failures"])
                print(f"{row['id']}: {row['status']}{' - ' + detail if detail else ''}")
    finally:
        if owned:
            await worker.close()
    return results, summarize(results)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Live personal memory extraction eval (#451)")
    parser.add_argument("--cases", type=Path, default=DEFAULT_CASES)
    parser.add_argument("--provider", choices=("openai", "gemini", "openrouter"))
    parser.add_argument("--model")
    parser.add_argument("--gemini-thinking-level", choices=("minimal", "low", "medium", "high"))
    parser.add_argument("--id", action="append", help="run only this case id (repeatable)")
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--repeat", type=int, default=1)
    parser.add_argument("--usage-log", default="")
    parser.add_argument("--results-dir", type=Path, default=DEFAULT_RESULTS)
    return parser


async def main_async(argv: list[str] | None = None) -> None:
    load_dotenv(Path.cwd() / ".env.local", override=False)
    load_dotenv(Path.cwd() / ".env", override=False)
    parser = _parser()
    args = parser.parse_args(argv)
    if args.limit < 0 or not 1 <= args.repeat <= 10:
        parser.error("--limit은 0 이상, --repeat은 1~10이어야 합니다.")
    results, summary = await run(args)
    args.results_dir.mkdir(parents=True, exist_ok=True)
    path = args.results_dir / (
        "memory-extraction-" + datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ") + ".json"
    )
    path.write_text(
        json.dumps({"summary": summary, "results": results}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps({key: summary[key] for key in ("runs", "cases", "status", "pass_rate")},
                     ensure_ascii=False))
    print(f"report: {path}")


def main() -> None:
    asyncio.run(main_async())


if __name__ == "__main__":
    main()

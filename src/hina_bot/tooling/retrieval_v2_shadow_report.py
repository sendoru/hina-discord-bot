"""Summarize content-free Retrieval v2 shadow/active telemetry JSONL."""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from statistics import mean


def _mean(rows, key):
    values = [
        row.get(key)
        for row in rows
        if isinstance(row.get(key), (int, float))
        and not isinstance(row.get(key), bool)
    ]
    return round(mean(values), 3) if values else None


def build_report(rows: list[dict]) -> dict:
    selected = [
        row for row in rows
        if row.get("operation") in {"retrieval_v2.shadow", "retrieval_v2.active"}
    ]
    statuses = Counter(str(row.get("status") or "unknown") for row in selected)
    completed = [row for row in selected if row.get("status") == "completed"]
    embedding_tokens = [
        row.get("retrieval_v2_embedding_prompt_tokens")
        for row in completed
        if isinstance(row.get("retrieval_v2_embedding_prompt_tokens"), int)
    ]
    embedding_requests = sum(
        int(row.get("retrieval_v2_embedding_requests") or 0)
        for row in completed
    )
    cache_hits = sum(int(row.get("retrieval_v2_cache_hits") or 0) for row in completed)
    cache_misses = sum(int(row.get("retrieval_v2_cache_misses") or 0) for row in completed)
    cache_total = cache_hits + cache_misses

    return {
        "schema_version": 1,
        "turns": len(selected),
        "completed": len(completed),
        "status_counts": dict(sorted(statuses.items())),
        "mode_counts": dict(sorted(Counter(
            str(row.get("retrieval_v2_mode") or "unknown")
            for row in selected
        ).items())),
        "mean_overlap_rate": _mean(completed, "retrieval_v2_overlap_rate"),
        "mean_elapsed_ms": _mean(completed, "retrieval_v2_elapsed_ms"),
        "zero_result_rate": (
            round(sum(bool(row.get("retrieval_v2_zero_result")) for row in completed)
                  / len(completed), 4)
            if completed else None
        ),
        "relation_hit_rate": (
            round(sum(bool(row.get("retrieval_v2_relation_hit")) for row in completed)
                  / len(completed), 4)
            if completed else None
        ),
        "evidence_sufficient_rate": (
            round(sum(bool(row.get("retrieval_v2_evidence_sufficient")) for row in completed)
                  / len(completed), 4)
            if completed else None
        ),
        "mean_selected": {
            lane: _mean(completed, f"retrieval_v2_selected_{lane}")
            for lane in ("factual", "relation", "ambient", "reaction")
        },
        "mean_factual_lexical_selected": _mean(
            completed, "retrieval_v2_factual_lexical_selected"
        ),
        "mean_factual_semantic_selected": _mean(
            completed, "retrieval_v2_factual_semantic_selected"
        ),
        "embedding": {
            "prompt_tokens": (
                sum(embedding_tokens)
                if len(embedding_tokens) == len(completed)
                else None
            ),
            "requests": embedding_requests,
            "cache_hits": cache_hits,
            "cache_misses": cache_misses,
            "cache_hit_rate": (
                round(cache_hits / cache_total, 4) if cache_total else None
            ),
        },
        "fallback_reasons": dict(sorted(Counter(
            str(row.get("retrieval_v2_fallback_reason"))
            for row in selected
            if row.get("retrieval_v2_fallback_reason")
        ).items())),
        "calibration_status": dict(sorted(Counter(
            str(row.get("retrieval_v2_calibration_status"))
            for row in completed
            if row.get("retrieval_v2_calibration_status")
        ).items())),
        "factual_invocation": dict(sorted(Counter(
            str(row.get("retrieval_v2_factual_invocation"))
            for row in completed
            if row.get("retrieval_v2_factual_invocation")
        ).items())),
        "evidence_reasons": dict(sorted(Counter(
            str(row.get("retrieval_v2_evidence_reason"))
            for row in completed
            if row.get("retrieval_v2_evidence_reason")
        ).items())),
    }


def read_rows(path: Path) -> list[dict]:
    if not path.exists():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            rows.append(value)
    return rows


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("path", type=Path, nargs="?", default=Path("data/logs/usage.jsonl"))
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)

    report = build_report(read_rows(args.path))
    rendered = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    else:
        print(rendered, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Opt-in live Gemini evaluation for Retrieval v2 ambient character insights."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from collections.abc import Sequence
from dataclasses import asdict
from hashlib import sha256
from math import isfinite
from pathlib import Path
from statistics import mean, median
from time import perf_counter

from hina_bot.ai.embedding_backend import GeminiEmbeddingBackend, GeminiEmbeddingConfig
from hina_bot.core.ambient_retrieval import (
    AmbientConfig,
    ambient_query,
    eligible_ambient_candidates,
    rank_ambient,
)
from hina_bot.core.entity_resolution import HINA_ENTITY_ID
from hina_bot.core.knowledge_retrieval import KnowledgeCandidate
from hina_bot.core.lore import LoreIndex, read_jsonl
from hina_bot.core.retrieval_v2 import (
    RetrievalRequest,
    UsageBudget,
    pack_ranked,
)
from hina_bot.core.semantic_retrieval import (
    EmbeddingBackend,
    EmbeddingUsage,
    SemanticCalibration,
    SemanticIndex,
)

LABELS = ("positive", "hard_negative", "unrelated")


def validate_cases(
    cases: Sequence[dict],
    candidates: Sequence[KnowledgeCandidate],
) -> None:
    ids = {row.candidate_id for row in candidates}
    seen = set()
    if not cases or not candidates or len(ids) != len(candidates):
        raise ValueError("empty ambient fixtures/corpus or duplicate ids")
    for case in cases:
        if case["id"] in seen or case["split"] not in {"calibration", "evaluation"}:
            raise ValueError("invalid ambient fixture id or split")
        seen.add(case["id"])
        if not isinstance(case["visible_text"], str) or not case["visible_text"].strip():
            raise ValueError("ambient fixture requires visible_text")
        recent = case.get("recent_same_speaker", [])
        if not isinstance(recent, list) or len(recent) > 2:
            raise ValueError("ambient fixture recent_same_speaker must contain at most two turns")
        axes = case.get("relationship_axes", {})
        request = RetrievalRequest(
            case["visible_text"],
            case.get("retrieval_text", case["visible_text"]),
            anchor_text=case.get("anchor_text", ""),
            entities=(HINA_ENTITY_ID,),
        )
        # Reuse runtime validation for axes/bounds and scene construction.
        ambient_query(
            request,
            recent_same_speaker=recent,
            relationship_axes=axes,
        )
        labels = [identifier for label in LABELS for identifier in case[label]]
        if len(labels) != len(set(labels)) or not set(labels) <= ids:
            raise ValueError("overlapping ambient labels or missing candidate ids")
    if not {"calibration", "evaluation"} <= {case["split"] for case in cases}:
        raise ValueError("ambient fixtures need calibration and evaluation splits")


def distribution(values: list[float]) -> dict:
    if not values:
        return {"count": 0}
    return {
        "count": len(values),
        "min": min(values),
        "median": median(values),
        "mean": mean(values),
        "max": max(values),
    }


def trial_calibration(
    key: str,
    values: dict[str, list[float]],
) -> SemanticCalibration:
    negatives = values["hard_negative"] + values["unrelated"]
    positives = values["positive"]
    if not negatives or not positives:
        raise ValueError("ambient calibration requires positive and negative labels")
    reject = max(negatives)
    if reject >= 1:
        raise ValueError("ambient negative cosine reaches 1")
    strong = max(max(positives), reject + (1 - reject) / 2)
    return SemanticCalibration(key, reject, strong)


async def evaluate(
    backend: EmbeddingBackend,
    cases: Sequence[dict],
    candidates: Sequence[KnowledgeCandidate],
    *,
    min_score: float = 0.75,
    top_n: int = 2,
    max_chars: int = 900,
    reject: float | None = None,
    strong: float | None = None,
    usd_per_million_tokens: float | None = None,
) -> dict:
    rows = eligible_ambient_candidates(candidates)
    validate_cases(cases, rows)
    if top_n <= 0 or max_chars <= 0:
        raise ValueError("ambient top_n/max_chars must be positive")
    if not 0 < min_score <= 1 or (reject is None) != (strong is None):
        raise ValueError("invalid ambient threshold options")
    if usd_per_million_tokens is not None and (
        not isfinite(usd_per_million_tokens) or usd_per_million_tokens < 0
    ):
        raise ValueError("price must be finite and non-negative")

    index = SemanticIndex(backend)
    warmup = await index.warm(rows)
    candidate_usage = warmup.usage
    query_usage = EmbeddingUsage()
    measured = []
    samples = {
        split: {label: [] for label in LABELS}
        for split in ("calibration", "evaluation")
    }

    for case in cases:
        request = RetrievalRequest(
            case["visible_text"],
            case.get("retrieval_text", case["visible_text"]),
            anchor_text=case.get("anchor_text", ""),
            entities=(HINA_ENTITY_ID,),
        )
        meaning = ambient_query(
            request,
            recent_same_speaker=case.get("recent_same_speaker", ()),
            relationship_axes=case.get("relationship_axes"),
        )
        started = perf_counter()
        result = await index.search(meaning, rows, top_k=len(rows))
        elapsed_ms = (perf_counter() - started) * 1000
        candidate_usage += result.candidate_usage
        query_usage += result.query_usage
        cosines = {rows[hit.order].candidate_id: hit.cosine for hit in result.hits}
        labeled = {
            label: {
                identifier: cosines[identifier]
                for identifier in case[label]
            }
            for label in LABELS
        }
        for label, values in labeled.items():
            samples[case["split"]][label].extend(values.values())
        measured.append((case, request, result, {
            "id": case["id"],
            "split": case["split"],
            "semantic_query": meaning,
            "semantic_query_character_count": len(meaning),
            "query_embedding": {
                **asdict(result.query_usage),
                "latency_ms": result.query_embedding_ms,
            },
            "candidate_embedding": {
                **asdict(result.candidate_usage),
                "warmup_ms": result.candidate_warmup_ms,
            },
            "request_count": (
                result.query_usage.request_count
                + result.candidate_usage.request_count
            ),
            "cosines": labeled,
            "semantic_order": [
                {"id": rows[hit.order].candidate_id, "cosine": hit.cosine}
                for hit in result.hits
            ],
            "cache_hits": result.cache_hits,
            "cache_misses": result.cache_misses,
            "elapsed_ms": elapsed_ms,
        }))

    calibration = (
        SemanticCalibration(backend.cache_key, reject, strong)
        if reject is not None
        else trial_calibration(backend.cache_key, samples["calibration"])
    )
    config = AmbientConfig(calibration=calibration, min_score=min_score)
    reports = []
    for case, _, result, report in measured:
        ranked = rank_ambient(rows, result.hits, config)
        selected = pack_ranked(ranked, UsageBudget(top_n, max_chars))
        selected_ids = [row.candidate.candidate_id for row in selected]
        labeled_ids = set().union(*(case[label] for label in LABELS))
        report["selected"] = selected_ids
        report["scores"] = [row.score for row in selected]
        report["positive_hits"] = len(set(selected_ids) & set(case["positive"]))
        report["positive_total"] = len(case["positive"])
        report["hard_negative_hits"] = len(
            set(selected_ids) & set(case["hard_negative"])
        )
        report["unrelated_hits"] = len(set(selected_ids) & set(case["unrelated"]))
        report["unjudged"] = [
            identifier for identifier in selected_ids
            if identifier not in labeled_ids
        ]
        report["zero_result"] = not selected_ids
        reports.append(report)

    metrics = {}
    for split in ("calibration", "evaluation"):
        subset = [report for report in reports if report["split"] == split]
        positive_total = sum(row["positive_total"] for row in subset)
        negative_cases = [row for row in subset if not row["positive_total"]]
        metrics[split] = {
            "positive_recall_at_n": (
                sum(row["positive_hits"] for row in subset) / positive_total
                if positive_total else None
            ),
            "hard_negative_hits": sum(row["hard_negative_hits"] for row in subset),
            "unrelated_hits": sum(row["unrelated_hits"] for row in subset),
            "unjudged_selected": sum(len(row["unjudged"]) for row in subset),
            "zero_result_accuracy": (
                sum(row["zero_result"] for row in negative_cases) / len(negative_cases)
                if negative_cases else None
            ),
        }

    total_usage = candidate_usage + query_usage
    report = {
        "schema_version": 1,
        "status": "experimental_not_production_calibration",
        "backend_key": backend.cache_key,
        "dimensions": backend.dimensions,
        "corpus_size": len(rows),
        "fixture_count": len(cases),
        "corpus_sha256": sha256(json.dumps([
            (row.candidate_id, row.semantic_representation)
            for row in rows
        ], ensure_ascii=False).encode()).hexdigest(),
        "fixtures_sha256": sha256(json.dumps(
            cases, ensure_ascii=False, sort_keys=True,
        ).encode()).hexdigest(),
        "candidate_warmup": asdict(warmup),
        "trial_thresholds": {
            "reject": calibration.reject,
            "strong": calibration.strong,
            "min_score": min_score,
        },
        "packing": {"max_items": top_n, "max_chars": max_chars},
        "distributions": {
            split: {
                label: distribution(values)
                for label, values in labels.items()
            }
            for split, labels in samples.items()
        },
        "cases": reports,
        "metrics": metrics,
        "usage": {
            "candidate": asdict(candidate_usage),
            "query": asdict(query_usage),
            "total": asdict(total_usage),
        },
        "cost": {
            "usd_per_million_tokens": usd_per_million_tokens,
            "estimated_usd": (
                total_usage.prompt_token_count
                * usd_per_million_tokens / 1_000_000
                if total_usage.prompt_token_count is not None
                and usd_per_million_tokens is not None
                else None
            ),
            "price_source": (
                "operator_supplied"
                if usd_per_million_tokens is not None
                else None
            ),
        },
    }
    return report


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--cases",
        type=Path,
        default=Path("evals/retrieval_v2_ambient.jsonl"),
    )
    parser.add_argument("--lore", default="")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--model", default="gemini-embedding-2")
    parser.add_argument("--dimensions", type=int, default=768)
    parser.add_argument("--revision", default="1")
    parser.add_argument("--batch-size", type=int, default=50)
    parser.add_argument("--min-score", type=float, default=0.75)
    parser.add_argument("--top-n", type=int, default=2)
    parser.add_argument("--max-chars", type=int, default=900)
    parser.add_argument("--reject", type=float)
    parser.add_argument("--strong", type=float)
    parser.add_argument("--usd-per-million-tokens", type=float)
    parser.add_argument("--validate-only", action="store_true")
    args = parser.parse_args(argv)

    try:
        candidates = LoreIndex.load(args.lore).candidates(include_community=False)
        rows = eligible_ambient_candidates(candidates)
        cases = read_jsonl(args.cases)
        validate_cases(cases, rows)
        AmbientConfig(min_score=args.min_score)
        if args.top_n <= 0 or args.max_chars <= 0:
            raise ValueError("invalid packing")
        if (args.reject is None) != (args.strong is None):
            raise ValueError("reject/strong must be paired")
        if args.reject is not None:
            SemanticCalibration("validation", args.reject, args.strong)
        config = GeminiEmbeddingConfig(
            args.model,
            args.dimensions,
            args.revision,
            batch_size=args.batch_size,
        )
    except (ValueError, KeyError, TypeError, OSError):
        parser.error("invalid ambient fixture, corpus or configuration")

    if args.validate_only:
        print(json.dumps({
            "valid": True,
            "cases": len(cases),
            "ambient_corpus_size": len(rows),
        }))
        return 0

    key = os.environ.get("GEMINI_API_KEY", "")
    if not key:
        parser.error("set GEMINI_API_KEY in the environment")

    async def run():
        backend = GeminiEmbeddingBackend(key, config=config)
        try:
            return await evaluate(
                backend,
                cases,
                candidates,
                min_score=args.min_score,
                top_n=args.top_n,
                max_chars=args.max_chars,
                reject=args.reject,
                strong=args.strong,
                usd_per_million_tokens=args.usd_per_million_tokens,
            )
        finally:
            await backend.close()

    try:
        report = asyncio.run(run())
    except Exception:  # noqa: BLE001 - do not leak provider/request content
        parser.exit(1, "ambient calibration failed; no report written\n")

    rendered = json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    else:
        print(rendered, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

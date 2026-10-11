"""Live Gemini calibration report for Retrieval v2 ambient character insights."""

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
    AmbientSceneContext,
    ambient_query,
    eligible_ambient_candidates,
)
from hina_bot.core.entity_resolution import HINA_ENTITY_ID
from hina_bot.core.lore import LoreIndex, read_jsonl
from hina_bot.core.retrieval_v2 import RetrievalIntent, RetrievalRequest
from hina_bot.core.semantic_retrieval import (
    EmbeddingBackend,
    EmbeddingUsage,
    SemanticCalibration,
    SemanticIndex,
)

LABELS = ("positive", "hard_negative", "unrelated")


def _request(case: dict) -> RetrievalRequest:
    return RetrievalRequest(
        case["visible_text"],
        case["visible_text"],
        anchor_text=case.get("anchor_text", ""),
        entities=(HINA_ENTITY_ID,),
        intent=RetrievalIntent.CONVERSATION,
    )


def _scene(case: dict) -> AmbientSceneContext:
    return AmbientSceneContext(
        HINA_ENTITY_ID,
        recent_same_speaker=tuple(case.get("recent_same_speaker", ())),
        relationship_signal=case.get("relationship_signal", ""),
    )


def validate_cases(cases: Sequence[dict], candidate_ids: set[str]) -> None:
    if not cases or not candidate_ids:
        raise ValueError("ambient fixtures and corpus must be non-empty")
    seen = set()
    splits = set()
    for case in cases:
        if not isinstance(case, dict):
            raise TypeError("ambient fixture row must be an object")
        identifier = case.get("id")
        split = case.get("split")
        if not isinstance(identifier, str) or not identifier or identifier in seen:
            raise ValueError("ambient fixture id must be unique")
        if split not in {"calibration", "evaluation"}:
            raise ValueError("ambient fixture split must be calibration/evaluation")
        seen.add(identifier)
        splits.add(split)
        request = _request(case)
        query = ambient_query(request, _scene(case))
        if not query:
            raise ValueError("ambient fixture query must be non-empty")
        labels = []
        for label in LABELS:
            values = case.get(label)
            if not isinstance(values, list) or any(not isinstance(v, str) for v in values):
                raise ValueError("ambient fixture labels must be lists of ids")
            labels.extend(values)
        if len(labels) != len(set(labels)) or not set(labels) <= candidate_ids:
            raise ValueError("ambient fixture labels overlap or reference missing ids")
    if splits != {"calibration", "evaluation"}:
        raise ValueError("ambient fixtures require calibration and evaluation splits")


def _distribution(values: list[float]) -> dict:
    if not values:
        return {"count": 0}
    return {
        "count": len(values),
        "min": min(values),
        "median": median(values),
        "mean": mean(values),
        "max": max(values),
    }


def _trial_calibration(
    backend_key: str,
    samples: dict[str, list[float]],
) -> SemanticCalibration:
    negatives = samples["hard_negative"] + samples["unrelated"]
    positives = samples["positive"]
    if not negatives or not positives:
        raise ValueError("ambient calibration needs positive and negative samples")
    reject = max(negatives)
    if reject >= 1:
        raise ValueError("ambient negative cosine reaches 1")
    strong = max(max(positives), reject + (1 - reject) / 2)
    return SemanticCalibration(backend_key, reject, strong)


async def evaluate(
    backend: EmbeddingBackend,
    cases: Sequence[dict],
    *,
    lore: LoreIndex | None = None,
    min_score: float = 0.75,
    reject: float | None = None,
    strong: float | None = None,
    usd_per_million_tokens: float | None = None,
) -> dict:
    if not isfinite(min_score) or not 0 < min_score <= 1:
        raise ValueError("ambient min score must be in (0, 1]")
    if (reject is None) != (strong is None):
        raise ValueError("ambient reject/strong overrides must be paired")
    if usd_per_million_tokens is not None and (
        not isfinite(usd_per_million_tokens) or usd_per_million_tokens < 0
    ):
        raise ValueError("embedding price must be finite and non-negative")

    index_source = lore or LoreIndex.load()
    all_candidates = index_source.candidates(include_community=False)
    ambient = eligible_ambient_candidates(
        AmbientSceneContext(HINA_ENTITY_ID),
        all_candidates,
    )
    validate_cases(cases, {row.candidate_id for row in ambient})

    index = SemanticIndex(backend)
    warmup = await index.warm(ambient)
    candidate_usage = warmup.usage
    query_usage = EmbeddingUsage()
    calibration_samples = {label: [] for label in LABELS}
    reports = []

    for case in cases:
        request = _request(case)
        query = ambient_query(request, _scene(case))
        started = perf_counter()
        search = await index.search(query, ambient, top_k=len(ambient))
        elapsed_ms = (perf_counter() - started) * 1000
        candidate_usage += search.candidate_usage
        query_usage += search.query_usage
        cosines = {
            ambient[hit.order].candidate_id: hit.cosine
            for hit in search.hits
        }
        labeled = {
            label: {identifier: cosines[identifier] for identifier in case[label]}
            for label in LABELS
        }
        if case["split"] == "calibration":
            for label in LABELS:
                calibration_samples[label].extend(labeled[label].values())
        reports.append({
            "id": case["id"],
            "split": case["split"],
            "semantic_query_character_count": len(query),
            "cosines": labeled,
            "semantic_order": [
                {
                    "id": ambient[hit.order].candidate_id,
                    "cosine": hit.cosine,
                }
                for hit in search.hits
            ],
            "query_embedding": {
                **asdict(search.query_usage),
                "latency_ms": search.query_embedding_ms,
            },
            "candidate_embedding": {
                **asdict(search.candidate_usage),
                "latency_ms": search.candidate_warmup_ms,
            },
            "request_count": (
                search.query_usage.request_count
                + search.candidate_usage.request_count
            ),
            "cache_hits": search.cache_hits,
            "cache_misses": search.cache_misses,
            "elapsed_ms": elapsed_ms,
        })

    calibration = (
        SemanticCalibration(backend.cache_key, reject, strong)
        if reject is not None
        else _trial_calibration(backend.cache_key, calibration_samples)
    )

    metrics = {}
    for report, case in zip(reports, cases, strict=True):
        scores = [
            (
                item["id"],
                calibration.score(item["cosine"]),
            )
            for item in report["semantic_order"]
        ]
        selected = [identifier for identifier, score in scores if score >= min_score][:2]
        report["selected"] = selected
        report["selected_scores"] = [
            score for _, score in scores if score >= min_score
        ][:2]
        report["positive_hits"] = len(set(selected) & set(case["positive"]))
        report["hard_negative_hits"] = len(set(selected) & set(case["hard_negative"]))
        report["unrelated_hits"] = len(set(selected) & set(case["unrelated"]))
        report["zero_result"] = not selected

    for split in ("calibration", "evaluation"):
        subset = [
            (report, case)
            for report, case in zip(reports, cases, strict=True)
            if case["split"] == split
        ]
        positive_total = sum(len(case["positive"]) for _, case in subset)
        zero_cases = [(report, case) for report, case in subset if not case["positive"]]
        metrics[split] = {
            "positive_recall_at_2": (
                sum(report["positive_hits"] for report, _ in subset) / positive_total
                if positive_total else None
            ),
            "hard_negative_hits": sum(
                report["hard_negative_hits"] for report, _ in subset
            ),
            "unrelated_hits": sum(report["unrelated_hits"] for report, _ in subset),
            "zero_result_accuracy": (
                sum(report["zero_result"] for report, _ in zero_cases) / len(zero_cases)
                if zero_cases else None
            ),
        }

    total_usage = candidate_usage + query_usage
    return {
        "schema_version": 1,
        "status": "experimental_not_production_calibration",
        "backend_key": backend.cache_key,
        "dimensions": backend.dimensions,
        "corpus_size": len(ambient),
        "fixture_count": len(cases),
        "corpus_sha256": sha256(json.dumps([
            (row.candidate_id, row.semantic_representation)
            for row in ambient
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
        "calibration_distributions": {
            label: _distribution(values)
            for label, values in calibration_samples.items()
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
                total_usage.prompt_token_count * usd_per_million_tokens / 1_000_000
                if (
                    total_usage.prompt_token_count is not None
                    and usd_per_million_tokens is not None
                )
                else None
            ),
            "price_source": (
                "operator_supplied"
                if usd_per_million_tokens is not None
                else None
            ),
        },
    }


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
    parser.add_argument("--reject", type=float)
    parser.add_argument("--strong", type=float)
    parser.add_argument("--usd-per-million-tokens", type=float)
    parser.add_argument("--validate-only", action="store_true")
    args = parser.parse_args(argv)

    try:
        lore = LoreIndex.load(args.lore)
        candidates = lore.candidates(include_community=False)
        ambient = eligible_ambient_candidates(
            AmbientSceneContext(HINA_ENTITY_ID),
            candidates,
        )
        cases = read_jsonl(args.cases)
        validate_cases(cases, {row.candidate_id for row in ambient})
        config = GeminiEmbeddingConfig(
            args.model,
            args.dimensions,
            args.revision,
            batch_size=args.batch_size,
        )
        if not isfinite(args.min_score) or not 0 < args.min_score <= 1:
            raise ValueError("invalid min score")
        if (args.reject is None) != (args.strong is None):
            raise ValueError("unpaired threshold override")
        if args.reject is not None:
            SemanticCalibration("validation", args.reject, args.strong)
        if args.usd_per_million_tokens is not None and (
            not isfinite(args.usd_per_million_tokens)
            or args.usd_per_million_tokens < 0
        ):
            raise ValueError("invalid price")
    except (KeyError, OSError, TypeError, ValueError):
        parser.error("invalid ambient fixture, corpus or configuration")

    if args.validate_only:
        print(json.dumps({
            "valid": True,
            "cases": len(cases),
            "ambient_corpus_size": len(ambient),
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
                lore=lore,
                min_score=args.min_score,
                reject=args.reject,
                strong=args.strong,
                usd_per_million_tokens=args.usd_per_million_tokens,
            )
        finally:
            await backend.close()

    try:
        report = asyncio.run(run())
    except Exception:  # noqa: BLE001 - CLI boundary must not leak provider/request content
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

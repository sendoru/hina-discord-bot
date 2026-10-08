"""Opt-in live Gemini calibration report; no production settings are written."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from collections.abc import Sequence
from dataclasses import asdict, replace
from hashlib import sha256
from pathlib import Path
from statistics import mean, median
from time import perf_counter

from hina_bot.ai.embedding_backend import GeminiEmbeddingBackend, GeminiEmbeddingConfig
from hina_bot.ai.retrieval_request import build_retrieval_request
from hina_bot.ai.routing_plan import RoutingPlan
from hina_bot.core.hybrid_retrieval import (
    Fusion,
    HybridConfig,
    eligible_candidates,
    pack_facts,
    rank_hybrid,
)
from hina_bot.core.knowledge_retrieval import KnowledgeCandidate
from hina_bot.core.lore import LoreIndex, read_jsonl
from hina_bot.core.retrieval_v2 import RetrievalIntent, UsageBudget
from hina_bot.core.semantic_retrieval import (
    EmbeddingBackend,
    SemanticCalibration,
    SemanticIndex,
    semantic_query,
)

LABELS = ("positive", "hard_negative", "unrelated")


def validate_cases(cases: Sequence[dict], candidates: Sequence[KnowledgeCandidate]) -> None:
    ids = {row.candidate_id for row in candidates}
    seen = set()
    if not cases or len(ids) != len(candidates):
        raise ValueError("empty fixtures or duplicate corpus ids")
    for case in cases:
        if case["id"] in seen or case["split"] not in {"calibration", "evaluation"}:
            raise ValueError("invalid fixture id or split")
        seen.add(case["id"])
        RetrievalIntent(case["intent"])
        if not case["visible_text"].strip():
            raise ValueError("empty fixture query")
        labels = [identifier for label in LABELS for identifier in case[label]]
        if len(labels) != len(set(labels)) or not set(labels) <= ids:
            raise ValueError("overlapping labels or missing corpus ids")
    if not {"calibration", "evaluation"} <= {case["split"] for case in cases}:
        raise ValueError("both calibration and held-out evaluation cases are required")


def distribution(values: list[float]) -> dict:
    if not values:
        return {"count": 0}
    return {"count": len(values), "min": min(values), "median": median(values),
            "mean": mean(values), "max": max(values)}


def trial_calibration(key: str, values: dict[str, list[float]]) -> SemanticCalibration:
    """Conservative diagnostic fit on calibration split ONLY, never a production approval.

    Reject every labeled calibration negative. If distributions overlap, recall can be zero;
    report that failure instead of lowering the cutoff until the fixture passes.
    """
    negatives = values["hard_negative"] + values["unrelated"]
    if not negatives or not values["positive"]:
        raise ValueError("calibration needs positive and negative labels")
    reject = max(negatives)
    if reject >= 1:
        raise ValueError("negative cosine reaches 1; no separating trial threshold exists")
    strong = max(max(values["positive"]), reject + (1 - reject) / 2)
    return SemanticCalibration(key, reject, strong)


async def evaluate(
    backend: EmbeddingBackend, cases: Sequence[dict], candidates: Sequence[KnowledgeCandidate], *,
    compare_intent_hint: bool = False, top_n: int = 3,
    reject: float | None = None, strong: float | None = None,
) -> dict:
    validate_cases(cases, candidates)
    if top_n <= 0 or (reject is None) != (strong is None):
        raise ValueError("positive top_n and paired reject/strong overrides are required")
    index = SemanticIndex(backend)
    report = {"schema_version": 1, "backend_key": backend.cache_key,
              "dimensions": backend.dimensions, "corpus_size": len(candidates), "top_n": top_n,
              "corpus_sha256": sha256(json.dumps([
                  (row.candidate_id, row.semantic_representation) for row in candidates
              ], ensure_ascii=False).encode()).hexdigest(),
              "fixtures_sha256": sha256(json.dumps(cases, sort_keys=True,
                                                    ensure_ascii=False).encode()).hexdigest(),
              "status": "experimental_not_production_calibration", "variants": {}}
    for hint in ([False, True] if compare_intent_hint else [False]):
        variant = "intent_hint" if hint else "routing_text"
        samples = {split: {label: [] for label in LABELS}
                   for split in ("calibration", "evaluation")}
        measured = []
        for case in cases:
            visible = case["visible_text"]
            routing_text = case.get("retrieval_text", visible)
            request = build_retrieval_request(
                RoutingPlan(visible, routing_text, anchor=case.get("anchor_text", "")),
                call_prefixes=("히나야",),
                entities=tuple(case.get("entities", ())),
                required_entities=tuple(case.get("required_entities", ())),
            )
            request = replace(request, intent=RetrievalIntent(case["intent"]))
            rows = eligible_candidates(request, candidates)
            started = perf_counter()
            # Offline diagnostic deliberately measures all fixture intents, even those the
            # opt-in retriever short-circuits. Exactly one query call per case/variant.
            result = await index.search(semantic_query(request, intent_hint=hint), rows,
                                        top_k=len(rows))
            elapsed = (perf_counter() - started) * 1000
            scores = {rows[hit.order].candidate_id: hit.cosine for hit in result.hits}
            labeled = {label: {identifier: scores[identifier] for identifier in case[label]
                               if identifier in scores} for label in LABELS}
            for label, values in labeled.items():
                samples[case["split"]][label].extend(values.values())
            measured.append((case, request, rows, result, {
                "id": case["id"], "split": case["split"], "intent": request.intent.value,
                "cosines": labeled,
                "semantic_top": [{"id": rows[h.order].candidate_id, "cosine": h.cosine}
                                 for h in result.hits[:top_n]],
                "cache_hits": result.cache_hits, "cache_misses": result.cache_misses,
                "elapsed_ms": elapsed,
            }))
        calibration = (SemanticCalibration(backend.cache_key, reject, strong)
                       if reject is not None else
                       trial_calibration(backend.cache_key, samples["calibration"]))
        output = {"threshold_origin": "explicit_trial" if reject is not None else "calibration_split",
                  "trial_thresholds": {"reject": calibration.reject, "strong": calibration.strong},
                  "distributions": {split: {label: distribution(v) for label, v in values.items()}
                                    for split, values in samples.items()},
                  "fusion_settings": asdict(HybridConfig(calibration=calibration, intent_hint=hint)),
                  "cases": [], "metrics": {}}
        for case, request, rows, result, case_report in measured:
            methods = {}
            for method in ("lexical_only", *(fusion.value for fusion in Fusion)):
                config = HybridConfig(calibration=calibration,
                                      fusion=Fusion.LEXICAL_FIRST if method == "lexical_only"
                                      else Fusion(method))
                use_semantic = (method != "lexical_only"
                                and request.intent != RetrievalIntent.PROFILE)
                ranked = ([] if request.intent == RetrievalIntent.CONVERSATION else rank_hybrid(
                    request, rows, config, semantic_hits=result.hits if use_semantic else None,
                ))
                chosen = pack_facts(ranked, UsageBudget(top_n, 3200)).facts
                selected = [row.candidate.candidate_id for row in chosen]
                labeled_ids = set().union(*(case[label] for label in LABELS))
                methods[method] = {
                    "selected": selected, "scores": [row.score for row in chosen],
                    "positive_hits": len(set(selected) & set(case["positive"])),
                    "positive_total": len(case["positive"]),
                    "hard_negative_hits": len(set(selected) & set(case["hard_negative"])),
                    "hard_negative_total": len(case["hard_negative"]),
                    "unrelated_hits": len(set(selected) & set(case["unrelated"])),
                    "unjudged": [identifier for identifier in selected if identifier not in labeled_ids],
                    "zero_result": not selected,
                }
            case_report["fusion"] = methods
            output["cases"].append(case_report)
        for split in samples:
            subset = [c for c in output["cases"] if c["split"] == split]
            output["metrics"][split] = {}
            for method in ("lexical_only", *(fusion.value for fusion in Fusion)):
                results = [c["fusion"][method] for c in subset]
                positive_total = sum(r["positive_total"] for r in results)
                negatives = [r for r in results if not r["positive_total"]]
                hard_total = sum(r["hard_negative_total"] for r in results)
                profiles = [c["fusion"][method] for c in subset if c["intent"] == "profile"]
                profile_selected = sum(len(r["selected"]) for r in profiles)
                output["metrics"][split][method] = {
                    "positive_recall_at_n": (sum(r["positive_hits"] for r in results)
                                             / positive_total if positive_total else None),
                    "hard_negative_hits": sum(r["hard_negative_hits"] for r in results),
                    "hard_negative_rejection": (1 - sum(r["hard_negative_hits"] for r in results)
                                                / hard_total if hard_total else None),
                    "profile_labeled_precision": (sum(r["positive_hits"] for r in profiles)
                                                  / profile_selected if profile_selected else None),
                    "unrelated_hits": sum(r["unrelated_hits"] for r in results),
                    "unjudged_selected": sum(len(r["unjudged"]) for r in results),
                    "zero_result_accuracy": (sum(r["zero_result"] for r in negatives)
                                             / len(negatives) if negatives else None),
                }
        report["variants"][variant] = output
    return report


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases", type=Path, default=Path("evals/retrieval_v2_calibration.jsonl"))
    parser.add_argument("--lore", default="")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--model", default="gemini-embedding-2")
    parser.add_argument("--dimensions", type=int, default=768)
    parser.add_argument("--revision", default="1")
    parser.add_argument("--top-n", type=int, default=3)
    parser.add_argument("--reject", type=float)
    parser.add_argument("--strong", type=float)
    parser.add_argument("--compare-intent-hint", action="store_true")
    parser.add_argument("--validate-only", action="store_true")
    args = parser.parse_args(argv)
    try:
        candidates = LoreIndex.load(args.lore).candidates(include_community=False)
        cases = read_jsonl(args.cases)
        validate_cases(cases, candidates)
        if args.top_n <= 0 or (args.reject is None) != (args.strong is None):
            raise ValueError("invalid top-n or unpaired reject/strong")
        config = GeminiEmbeddingConfig(args.model, args.dimensions, args.revision)
        if args.reject is not None:
            SemanticCalibration("validation", args.reject, args.strong)
    except (ValueError, KeyError, TypeError, OSError):
        parser.error("invalid fixture, corpus or configuration; check the documented schema")
    if args.validate_only:
        print(json.dumps({"valid": True, "cases": len(cases), "corpus_size": len(candidates)}))
        return 0
    key = os.environ.get("GEMINI_API_KEY", "")
    if not key:
        parser.error("set GEMINI_API_KEY in the environment (never pass it on the command line)")

    async def run():
        backend = GeminiEmbeddingBackend(key, config=config)
        try:
            return await evaluate(backend, cases, candidates, top_n=args.top_n,
                                  compare_intent_hint=args.compare_intent_hint,
                                  reject=args.reject, strong=args.strong)
        finally:
            await backend.close()

    try:
        report = asyncio.run(run())
    except Exception:  # noqa: BLE001 - CLI boundary must not leak provider content
        # Fail closed: a provider failure must not be mislabeled as a live measurement.
        parser.exit(1, "calibration failed; no report written (check API access and fixtures)\n")
    rendered = json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    else:
        print(rendered, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

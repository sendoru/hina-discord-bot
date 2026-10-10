"""Run end-to-end Retrieval v2 rollout evals with measured Gemini thresholds."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from importlib.resources import files
from pathlib import Path

from hina_bot.ai.embedding_backend import GeminiEmbeddingBackend, GeminiEmbeddingConfig
from hina_bot.ai.retrieval_request import build_resolved_retrieval_request
from hina_bot.ai.routing_plan import RoutingPlan
from hina_bot.core.ambient_retrieval import AmbientSceneContext
from hina_bot.core.lore import LoreIndex, read_jsonl
from hina_bot.core.relationship_grounding import RelationshipGrounder
from hina_bot.core.retrieval_v2_runtime import RetrievalV2Budgets, retrieve_v2
from hina_bot.core.semantic_retrieval import SemanticCalibration, SemanticIndex

_SECTIONS = {"facts", "relations", "character_insights", "reactions"}


def _corpus():
    base = LoreIndex.load()
    supplement = LoreIndex.load(str(files("hina_bot").joinpath(
        "data/relationship_grounding.jsonl",
    )))
    candidates = [
        *supplement.candidates(include_community=False),
        *base.candidates(include_community=False),
    ]
    return candidates, RelationshipGrounder(candidates)


def validate_cases(cases, candidate_ids):
    seen = set()
    categories = set()
    for case in cases:
        identifier = case.get("id")
        if not isinstance(identifier, str) or not identifier or identifier in seen:
            raise ValueError("rollout fixture ids must be unique non-empty strings")
        seen.add(identifier)
        category = case.get("category")
        if not isinstance(category, str) or not category:
            raise ValueError("rollout fixture category is required")
        categories.add(category)
        if not isinstance(case.get("visible_text"), str) or not case["visible_text"].strip():
            raise ValueError("rollout fixture visible_text is required")
        if case.get("expected_section") not in _SECTIONS:
            raise ValueError("invalid rollout fixture section")
        expected = case.get("expected_any", [])
        forbidden = case.get("forbidden", [])
        if not isinstance(expected, list) or not isinstance(forbidden, list):
            raise ValueError("rollout fixture ids must be lists")
        if not set(expected + forbidden) <= candidate_ids:
            raise ValueError("rollout fixture references missing candidate ids")
        if case.get("expect_zero") and expected:
            raise ValueError("zero-result case cannot require a selected id")
        if "expected_evidence_state" in case and case["expected_evidence_state"] not in {
            "missing", "supported", "derived", "unknown", "conflict",
        }:
            raise ValueError("invalid expected evidence state")
    required = {"profile", "relation", "factual_semantic", "ambient", "reaction", "unrelated"}
    if not required <= categories:
        raise ValueError("rollout eval must cover all required retrieval categories")


async def evaluate(
    cases,
    *,
    backend,
    reject: float,
    strong: float,
    ambient_min: float,
):
    candidates, grounder = _corpus()
    validate_cases(cases, {row.candidate_id for row in candidates})
    index = SemanticIndex(backend)
    calibration = SemanticCalibration(backend.cache_key, reject, strong)
    budgets = RetrievalV2Budgets.from_legacy(6, 3200)

    reports = []
    prompt_tokens: int | None = 0
    requests = 0
    cache_hits = 0
    cache_misses = 0
    elapsed_ms = 0.0

    for case in cases:
        visible = case["visible_text"]
        routing = RoutingPlan(
            visible,
            case.get("routing_query", visible),
            case.get("anchor_text", ""),
            case.get("anchor_source", ""),
        )
        request = build_resolved_retrieval_request(
            routing,
            call_prefixes=("히나야",),
        )
        run = await retrieve_v2(
            request,
            candidates,
            grounder=grounder,
            semantic_index=index,
            calibration=calibration,
            calibration_status="eval_configured",
            scene=AmbientSceneContext("character.hina"),
            budgets=budgets,
            ambient_min_score=ambient_min,
        )
        sections = run.bundle.context_sections()
        selected = {
            name: [
                row.get("reference")
                for row in values
                if isinstance(row.get("reference"), str)
            ]
            for name, values in sections.items()
        }
        target = selected[case["expected_section"]]
        expected = set(case.get("expected_any", ()))
        forbidden = set(case.get("forbidden", ()))
        all_selected = set().union(*(set(values) for values in selected.values()))
        expected_ok = bool(expected & set(target)) if expected else True
        forbidden_ok = not bool(forbidden & all_selected)
        zero_ok = (not all_selected) if case.get("expect_zero") else True
        evidence_ok = (
            run.evidence.answer_state == case["expected_evidence_state"]
            if "expected_evidence_state" in case
            else True
        )
        passed = expected_ok and forbidden_ok and zero_ok and evidence_ok
        reports.append({
            "id": case["id"],
            "category": case["category"],
            "passed": passed,
            "intent": request.intent.value,
            "selected": selected,
            "evidence": {
                "sufficient": run.evidence.sufficient,
                "reason": run.evidence.reason,
                "predicate": run.evidence.predicate,
                "state": run.evidence.answer_state,
            },
            "factual_invocation": run.factual_invocation,
            "factual_semantic_status": run.factual_semantic_status,
            "ambient_semantic_status": run.ambient_semantic_status,
            "elapsed_ms": round(run.elapsed_ms, 3),
        })
        if prompt_tokens is None or run.embedding_prompt_tokens is None:
            prompt_tokens = None
        else:
            prompt_tokens += run.embedding_prompt_tokens
        requests += run.embedding_requests
        cache_hits += run.semantic_cache_hits
        cache_misses += run.semantic_cache_misses
        elapsed_ms += run.elapsed_ms

    return {
        "schema_version": 1,
        "status": "evaluation_only_not_production_approval",
        "backend_key": backend.cache_key,
        "thresholds": {
            "reject": reject,
            "strong": strong,
            "ambient_min_score": ambient_min,
        },
        "cases": reports,
        "summary": {
            "total": len(reports),
            "passed": sum(row["passed"] for row in reports),
            "failed": sum(not row["passed"] for row in reports),
            "embedding_prompt_tokens": prompt_tokens,
            "embedding_requests": requests,
            "cache_hits": cache_hits,
            "cache_misses": cache_misses,
            "elapsed_ms": round(elapsed_ms, 3),
        },
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--cases",
        type=Path,
        default=Path("evals/retrieval_v2_rollout.jsonl"),
    )
    parser.add_argument("--validate-only", action="store_true")
    parser.add_argument("--reject", type=float)
    parser.add_argument("--strong", type=float)
    parser.add_argument("--ambient-min", type=float, default=0.75)
    parser.add_argument("--model", default="gemini-embedding-2")
    parser.add_argument("--dimensions", type=int, default=768)
    parser.add_argument("--revision", default="1")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)

    candidates, _ = _corpus()
    cases = read_jsonl(args.cases)
    try:
        validate_cases(cases, {row.candidate_id for row in candidates})
        if not 0 < args.ambient_min <= 1:
            raise ValueError("ambient threshold must be in (0,1]")
    except (KeyError, TypeError, ValueError, OSError):
        parser.error("invalid Retrieval v2 rollout fixture")

    if args.validate_only:
        print(json.dumps({
            "valid": True,
            "cases": len(cases),
            "categories": sorted({case["category"] for case in cases}),
        }, ensure_ascii=False))
        return 0

    if args.reject is None or args.strong is None:
        parser.error("--reject and --strong from live calibration are required")
    try:
        SemanticCalibration("validation", args.reject, args.strong)
    except ValueError:
        parser.error("invalid measured semantic thresholds")

    key = os.environ.get("GEMINI_API_KEY", "").strip()
    if not key:
        parser.error("set GEMINI_API_KEY in the environment")

    async def run():
        backend = GeminiEmbeddingBackend(
            key,
            config=GeminiEmbeddingConfig(
                args.model,
                args.dimensions,
                args.revision,
            ),
        )
        try:
            return await evaluate(
                cases,
                backend=backend,
                reject=args.reject,
                strong=args.strong,
                ambient_min=args.ambient_min,
            )
        finally:
            await backend.close()

    try:
        report = asyncio.run(run())
    except Exception:  # noqa: BLE001 - keep provider/request content out of CLI errors
        parser.exit(1, "Retrieval v2 rollout evaluation failed; no report written\n")

    rendered = json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    else:
        print(rendered, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

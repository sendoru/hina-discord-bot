"""Run opt-in live evaluations for the semantic model-routing classifier."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from datetime import UTC, datetime
from pathlib import Path

from dotenv import load_dotenv

from hina_bot.ai.freshness import FreshnessMode
from hina_bot.ai.information_plan import InformationPlan
from hina_bot.ai.information_routing import InformationRoute
from hina_bot.ai.model_routing import build_model_plan
from hina_bot.ai.providers import create_provider_client, normalize_provider
from hina_bot.ai.routing_plan import RoutingPlan
from hina_bot.ai.rp_output_policy import ProvenanceMode
from hina_bot.ai.semantic_model_routing import SemanticModelRouter, semantic_result
from hina_bot.ai.usage import UsageLogger
from hina_bot.core.config import Settings

DEFAULT_CASES = Path("evals/model_routing_cases.jsonl")
DEFAULT_RESULTS = Path("data/evals")


def _key(provider: str) -> str:
    value = os.getenv("ROUTING_CLASSIFIER_API_KEY", "").strip()
    if value:
        return value
    variable = {
        "openai": "OPENAI_API_KEY",
        "gemini": "GEMINI_API_KEY",
        "openrouter": "OPENROUTER_API_KEY",
    }[provider]
    value = os.getenv(variable, "").strip()
    if not value:
        raise ValueError(f"ROUTING_CLASSIFIER_API_KEY 또는 {variable}가 필요합니다.")
    return value


def _read_cases(path: Path) -> list[dict]:
    cases = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            case = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"{path}:{line_number}: {exc}") from exc
        if (not isinstance(case.get("id"), str) or not case["id"]
                or not isinstance(case.get("input"), str) or not case["input"]
                or case.get("expected_level") not in {"low", "medium", "high"}):
            raise ValueError(
                f"{path}:{line_number}: id, input, expected_level(low|medium|high)이 필요합니다."
            )
        prior = case.get("prior_user_request", "")
        if not isinstance(prior, str):
            raise TypeError(f"{path}:{line_number}: prior_user_request는 문자열이어야 합니다.")
        cases.append(case)
    if len({case["id"] for case in cases}) != len(cases):
        raise ValueError("model routing eval case id가 중복됩니다.")
    return cases


def _information(case: dict) -> InformationPlan:
    text = case["input"]
    return InformationPlan(
        routing=RoutingPlan(
            text,
            text,
            prior_user_request=case.get("prior_user_request", ""),
        ),
        route=InformationRoute.GENERAL,
        references=(),
        freshness=FreshnessMode.STATIC,
        fact_question=False,
        search_mode="none",
        provenance=ProvenanceMode.SILENT,
    )


def _settings(args) -> Settings:
    primary_provider = normalize_provider(os.getenv("LLM_PROVIDER", "openai"))
    provider = normalize_provider(
        args.provider or os.getenv("ROUTING_CLASSIFIER_PROVIDER", "") or primary_provider
    )
    model = (
        args.model
        or os.getenv("ROUTING_CLASSIFIER_MODEL", "").strip()
        or (os.getenv("LLM_FAST_MODEL", "").strip() if provider == primary_provider else "")
        or (os.getenv("LLM_MODEL", "").strip() if provider == primary_provider else "")
    )
    if not model:
        raise ValueError("--model 또는 ROUTING_CLASSIFIER_MODEL이 필요합니다.")
    credential = _key(provider)
    return Settings(
        api_key=credential,
        discord_token="routing-eval-only",
        provider=primary_provider,
        model_routing_mode="adaptive",
        fast_model=os.getenv("LLM_FAST_MODEL", "").strip() or "fast-eval-tier",
        smart_model=os.getenv("LLM_SMART_MODEL", "").strip() or "smart-eval-tier",
        routing_classifier_mode="active",
        routing_classifier_provider=provider,
        routing_classifier_model=model,
        routing_classifier_api_key=credential,
        routing_classifier_timeout_seconds=args.timeout,
        routing_classifier_max_output_tokens=args.max_output_tokens,
        usage_log_path=args.usage_log,
    )


async def run(args) -> tuple[list[dict], dict]:
    settings = _settings(args)
    cases = _read_cases(args.cases)
    if args.limit:
        cases = cases[:args.limit]
    usage = UsageLogger(settings.usage_log_path)
    client = create_provider_client(
        settings,
        settings.routing_classifier_provider,
        credential=settings.routing_classifier_key(),
        timeout=settings.routing_classifier_timeout_seconds,
        max_retries=0,
        thinking_level="minimal",
    )
    router = SemanticModelRouter(settings, client, usage)
    results = []
    try:
        for case in cases:
            information = _information(case)
            baseline = build_model_plan(settings, information)
            outcome = await router.classify(information, baseline_tier=baseline.tier.value)
            predicted_level, predicted_codes, status = semantic_result(outcome)
            actual = build_model_plan(
                settings,
                information,
                semantic_level=predicted_level,
                semantic_codes=predicted_codes,
                semantic_route_mode="active",
                semantic_route_status=status,
                model_route_baseline_tier=baseline.tier.value,
            )
            expected = build_model_plan(
                settings,
                information,
                semantic_level=case["expected_level"],
                semantic_route_mode="active",
                semantic_route_status="expected",
                model_route_baseline_tier=baseline.tier.value,
            )
            row = {
                "id": case["id"],
                "expected_level": case["expected_level"],
                "predicted_level": predicted_level or None,
                "classifier_status": outcome.status,
                "baseline_tier": baseline.tier.value,
                "expected_tier": expected.tier.value,
                "predicted_tier": actual.tier.value,
                "level_match": predicted_level == case["expected_level"],
                "tier_match": actual.tier == expected.tier,
            }
            results.append(row)
            print(
                f"{row['id']}: level={predicted_level or outcome.status} "
                f"(expected {row['expected_level']}), tier={row['predicted_tier']}"
            )
    finally:
        await client.close()
        usage.close()

    summary = {
        "cases": len(results),
        "level_matches": sum(row["level_match"] for row in results),
        "tier_matches": sum(row["tier_match"] for row in results),
        "classifier_failures": sum(row["predicted_level"] is None for row in results),
        "missed_smart": sum(
            row["expected_tier"] == "smart" and row["predicted_tier"] != "smart"
            for row in results
        ),
        "unnecessary_smart": sum(
            row["expected_tier"] != "smart" and row["predicted_tier"] == "smart"
            for row in results
        ),
    }
    return results, summary


async def main_async() -> None:
    load_dotenv(Path.cwd() / ".env.local", override=False)
    load_dotenv(Path.cwd() / ".env", override=False)
    parser = argparse.ArgumentParser()
    parser.add_argument("--cases", type=Path, default=DEFAULT_CASES)
    parser.add_argument("--provider", choices=("openai", "gemini", "openrouter"))
    parser.add_argument("--model")
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--timeout", type=float, default=4.0)
    parser.add_argument("--max-output-tokens", type=int, default=256)
    parser.add_argument("--usage-log", default="")
    parser.add_argument("--results-dir", type=Path, default=DEFAULT_RESULTS)
    parser.add_argument("--fail-on-mismatch", action="store_true")
    args = parser.parse_args()
    if args.limit < 0 or not 0.25 <= args.timeout <= 30 or not 32 <= args.max_output_tokens <= 1024:
        parser.error("limit은 0 이상, timeout은 0.25~30, max output tokens는 32~1024여야 합니다.")

    results, summary = await run(args)
    args.results_dir.mkdir(parents=True, exist_ok=True)
    path = args.results_dir / (
        "model-routing-" + datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ") + ".json"
    )
    path.write_text(
        json.dumps({"summary": summary, "results": results}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(summary, ensure_ascii=False))
    print(f"report: {path}")
    if args.fail_on_mismatch and (
        summary["level_matches"] != summary["cases"]
        or summary["tier_matches"] != summary["cases"]
    ):
        raise SystemExit(1)


def main() -> None:
    asyncio.run(main_async())


if __name__ == "__main__":
    main()

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Iterable
from datetime import UTC, datetime
from math import ceil

from .telemetry import TelemetrySnapshot

_TOKEN_FIELDS = (
    "input_tokens",
    "output_tokens",
    "total_tokens",
    "cached_tokens",
    "reasoning_tokens",
)
_MEMORY_OPERATIONS = {
    "summarize_memory",
    "summarize_shared_memory",
    "extract_memory_items_shadow",
    "memory.shadow_extraction",
}
_NEAR_THRESHOLD = 0.25


def _parse_time(value: object) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _filter_time(rows: Iterable[dict[str, object]], after: str, before: str):
    after_dt = _parse_time(after)
    before_dt = _parse_time(before)
    result = []
    for row in rows:
        row_dt = _parse_time(row.get("at"))
        if after_dt and (row_dt is None or row_dt < after_dt):
            continue
        if before_dt and (row_dt is None or row_dt > before_dt):
            continue
        result.append(row)
    return result


def _api_row(row: dict[str, object]) -> bool:
    return isinstance(row.get("model"), str) and bool(row.get("model"))


def _matches_text(value: object, expected: str) -> bool:
    if not expected:
        return True
    return expected in str(value or "").lower()


def _metric(rows: list[dict[str, object]], field: str) -> dict[str, int]:
    values = [
        value
        for row in rows
        if isinstance((value := row.get(field)), int) and not isinstance(value, bool)
    ]
    return {
        "value": sum(values),
        "known": len(values),
        "missing": len(rows) - len(values),
    }


def _percentile(values: list[int], fraction: float) -> int | None:
    if not values:
        return None
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, ceil(len(ordered) * fraction) - 1))
    return ordered[index]


def _latency(rows: list[dict[str, object]]) -> dict[str, int | None]:
    values = [
        value
        for row in rows
        if isinstance((value := row.get("elapsed_ms")), int) and not isinstance(value, bool)
    ]
    return {
        "known": len(values),
        "missing": len(rows) - len(values),
        "average": round(sum(values) / len(values)) if values else None,
        "p50": _percentile(values, 0.50),
        "p95": _percentile(values, 0.95),
        "max": max(values) if values else None,
    }


def _counter(rows: Iterable[dict[str, object]], field: str) -> dict[str, int]:
    values = Counter(
        str(value)
        for row in rows
        if (value := row.get(field)) not in (None, "")
    )
    return dict(sorted(values.items(), key=lambda item: (-item[1], item[0])))


def _nested_values(rows: Iterable[dict[str, object]], field: str) -> dict[str, int]:
    values = Counter()
    for row in rows:
        raw = row.get(field)
        if isinstance(raw, list):
            values.update(str(value) for value in raw if str(value))
        elif isinstance(raw, dict):
            values.update(str(key) for key, value in raw.items() if value)
    return dict(sorted(values.items(), key=lambda item: (-item[1], item[0])))


def _aggregate_calls(rows: list[dict[str, object]], field: str) -> list[dict[str, object]]:
    buckets: dict[str, list[dict[str, object]]] = defaultdict(list)
    for row in rows:
        value = row.get(field)
        key = str(value) if value not in (None, "") else "(unknown)"
        buckets[key].append(row)

    result = []
    for key, bucket in buckets.items():
        total = _metric(bucket, "total_tokens")
        result.append(
            {
                "name": key,
                "calls": len(bucket),
                "errors": sum(row.get("status") == "error" for row in bucket),
                "total_tokens": total,
                "latency": _latency(bucket),
                "web_search_calls": _metric(bucket, "web_search_calls"),
                "empty_response_retries": sum(
                    int(row.get("empty_response_retries") or 0)
                    for row in bucket
                    if isinstance(row.get("empty_response_retries"), int)
                ),
            }
        )
    result.sort(key=lambda row: (-int(row["calls"]), str(row["name"])))
    return result


def _route_margin_buckets(rows: list[dict[str, object]]) -> list[dict[str, object]]:
    buckets = [
        ("≤ -1.0", lambda value: value <= -1.0),
        ("-1.0 .. -0.25", lambda value: -1.0 < value < -0.25),
        ("-0.25 .. 0", lambda value: -0.25 <= value < 0),
        ("0 .. 0.25", lambda value: 0 <= value <= 0.25),
        ("0.25 .. 1.0", lambda value: 0.25 < value < 1.0),
        ("≥ 1.0", lambda value: value >= 1.0),
    ]
    values = [
        float(value)
        for row in rows
        if isinstance((value := row.get("model_route_margin")), int | float)
        and not isinstance(value, bool)
    ]
    return [
        {"label": label, "count": sum(predicate(value) for value in values)}
        for label, predicate in buckets
    ]


def _route_components(rows: list[dict[str, object]]) -> list[dict[str, object]]:
    values: dict[str, list[float]] = defaultdict(list)
    for row in rows:
        components = row.get("model_route_components")
        if not isinstance(components, dict):
            continue
        for key, value in components.items():
            if isinstance(value, int | float) and not isinstance(value, bool):
                values[str(key)].append(float(value))
    return [
        {
            "name": key,
            "known": len(items),
            "average": round(sum(items) / len(items), 3),
            "max": round(max(items), 3),
        }
        for key, items in sorted(values.items())
    ]


def _transition_counts(
    rows: Iterable[dict[str, object]], source: str, target: str
) -> list[dict[str, object]]:
    values = Counter()
    missing = 0
    for row in rows:
        left = row.get(source)
        right = row.get(target)
        if left in (None, "") or right in (None, ""):
            missing += 1
            continue
        values[(str(left), str(right))] += 1
    result = [
        {"from": left, "to": right, "count": count}
        for (left, right), count in values.items()
    ]
    result.sort(key=lambda row: (-int(row["count"]), str(row["from"]), str(row["to"])))
    return {"rows": result, "missing": missing}


def _daily(rows: list[dict[str, object]]) -> list[dict[str, object]]:
    buckets: dict[tuple[str, str, str], list[dict[str, object]]] = defaultdict(list)
    for row in rows:
        at = _parse_time(row.get("at"))
        day = at.date().isoformat() if at else "(unknown)"
        provider = str(row.get("provider") or "(unknown)")
        model = str(row.get("model") or "(unknown)")
        buckets[(day, provider, model)].append(row)

    result = []
    for (day, provider, model), bucket in buckets.items():
        result.append(
            {
                "day": day,
                "provider": provider,
                "model": model,
                "calls": len(bucket),
                "errors": sum(row.get("status") == "error" for row in bucket),
                "tokens": _metric(bucket, "total_tokens"),
                "latency": _latency(bucket),
            }
        )
    result.sort(key=lambda row: (str(row["day"]), str(row["provider"]), str(row["model"])), reverse=True)
    return result


def build_analytics(
    snapshot: TelemetrySnapshot,
    *,
    operation: str = "",
    model: str = "",
    provider: str = "",
    after: str = "",
    before: str = "",
) -> dict[str, object]:
    operation = operation.strip().lower()
    model = model.strip().lower()
    provider = provider.strip().lower()
    after = after.strip()
    before = before.strip()

    time_usage = _filter_time(snapshot.usage, after, before)
    api_rows = [row for row in time_usage if _api_row(row)]
    filtered_api = [
        row
        for row in api_rows
        if _matches_text(row.get("operation"), operation)
        and _matches_text(row.get("model"), model)
        and _matches_text(row.get("provider"), provider)
    ]

    # Routing/search describe answer decisions, so operation filters do not erase them.
    answer_rows = [
        row
        for row in api_rows
        if row.get("operation") == "answer"
        and _matches_text(row.get("model"), model)
        and _matches_text(row.get("provider"), provider)
    ]
    classifier_rows = [
        row
        for row in api_rows
        if row.get("operation") == "model_route_classify"
        and _matches_text(row.get("model"), model)
        and _matches_text(row.get("provider"), provider)
    ]
    shadow_rows = [
        row
        for row in time_usage
        if row.get("operation") == "model_route_shadow"
        and _matches_text(row.get("routing_classifier_provider"), provider)
    ]

    tokens = {field: _metric(filtered_api, field) for field in _TOKEN_FIELDS}
    known_route_margins = [
        float(row["model_route_margin"])
        for row in answer_rows
        if isinstance(row.get("model_route_margin"), int | float)
        and not isinstance(row.get("model_route_margin"), bool)
    ]

    exchanges = _filter_time(snapshot.exchanges, after, before)
    exchange_completeness = Counter()
    for row in exchanges:
        value = row.get("usage_complete")
        if value is True:
            exchange_completeness["complete"] += 1
        elif value is False:
            exchange_completeness["partial"] += 1
        else:
            exchange_completeness["unknown"] += 1

    search_outcomes = Counter()
    for row in answer_rows:
        mode = str(row.get("search_route_mode") or "(unknown)")
        used = row.get("web_search_used")
        if used is True:
            outcome = f"{mode} → used"
        elif used is False:
            outcome = f"{mode} → unused"
        else:
            outcome = f"{mode} → unknown"
        search_outcomes[outcome] += 1

    memory_rows = [
        row
        for row in filtered_api
        if str(row.get("operation") or "") in _MEMORY_OPERATIONS
        or "memory" in str(row.get("operation") or "")
        or "summary" in str(row.get("operation") or "")
    ]

    return {
        "available": {
            "oldest_at": snapshot.oldest_at,
            "newest_at": snapshot.newest_at,
        },
        "filters": {
            "operation": operation,
            "model": model,
            "provider": provider,
            "after": after,
            "before": before,
        },
        "usage": {
            "api_calls": len(filtered_api),
            "errors": sum(row.get("status") == "error" for row in filtered_api),
            "tokens": tokens,
            "latency": _latency(filtered_api),
            "web_search_calls": _metric(filtered_api, "web_search_calls"),
            "empty_response_retries": sum(
                int(row.get("empty_response_retries") or 0)
                for row in filtered_api
                if isinstance(row.get("empty_response_retries"), int)
            ),
            "operations": _aggregate_calls(filtered_api, "operation"),
            "models": _aggregate_calls(filtered_api, "model"),
            "providers": _aggregate_calls(filtered_api, "provider"),
            "daily": _daily(filtered_api),
            "memory_calls": len(memory_rows),
            "memory_tokens": _metric(memory_rows, "total_tokens"),
        },
        "exchanges": {
            "count": len(exchanges),
            "usage_completeness": dict(exchange_completeness),
            "calls": _metric(exchanges, "calls"),
            "tokens": {field: _metric(exchanges, field) for field in _TOKEN_FIELDS},
            "latency": _latency(exchanges),
        },
        "routing": {
            "answer_rows": len(answer_rows),
            "tiers": _counter(answer_rows, "model_tier"),
            "baseline_to_final": _transition_counts(
                answer_rows, "model_route_baseline_tier", "model_tier"
            ),
            "semantic_status": _counter(answer_rows, "semantic_route_status"),
            "semantic_level": _counter(answer_rows, "semantic_route_level"),
            "decision_source": _counter(answer_rows, "model_route_decision_source"),
            "policies": _counter(answer_rows, "model_route_policy"),
            "reasons": _nested_values(answer_rows, "model_route_reasons"),
            "components": _route_components(answer_rows),
            "margin_buckets": _route_margin_buckets(answer_rows),
            "near_threshold": sum(abs(value) <= _NEAR_THRESHOLD for value in known_route_margins),
            "known_margins": len(known_route_margins),
            "missing_margins": len(answer_rows) - len(known_route_margins),
            "classifier": {
                "calls": len(classifier_rows),
                "errors": sum(row.get("status") == "error" for row in classifier_rows),
                "tokens": _metric(classifier_rows, "total_tokens"),
                "latency": _latency(classifier_rows),
                "providers": _counter(classifier_rows, "provider"),
            },
            "shadow": {
                "count": len(shadow_rows),
                "tier_transitions": _transition_counts(
                    shadow_rows, "model_route_baseline_tier", "model_route_proposed_tier"
                ),
                "search_transitions": _transition_counts(
                    shadow_rows, "search_route_baseline_mode", "search_route_proposed_mode"
                ),
                "semantic_level": _counter(shadow_rows, "semantic_route_level"),
                "semantic_web_need": _counter(shadow_rows, "semantic_web_need"),
            },
        },
        "search": {
            "answer_rows": len(answer_rows),
            "baseline_mode": _counter(answer_rows, "search_route_baseline_mode"),
            "final_mode": _counter(answer_rows, "search_route_mode"),
            "decision_source": _counter(answer_rows, "search_route_decision_source"),
            "locked": _counter(answer_rows, "search_route_locked"),
            "locked_reasons": _counter(answer_rows, "search_route_reason"),
            "semantic_web_need": _counter(answer_rows, "semantic_web_need"),
            "semantic_web_uncertain": _counter(answer_rows, "semantic_web_uncertain"),
            "actual_web_used": _counter(answer_rows, "web_search_used"),
            "outcomes": dict(sorted(search_outcomes.items(), key=lambda item: (-item[1], item[0]))),
        },
    }


__all__ = ["build_analytics"]

"""Validate presentation filters without silently changing requested conditions."""
from __future__ import annotations

import math
from collections.abc import Mapping

from .timeutils import parse_local_time


def confidence_value(value: str) -> float | None:
    if not value.strip():
        return None
    try:
        number = float(value)
    except ValueError:
        return None
    return number if math.isfinite(number) and 0 <= number <= 1 else None


def validate_filters(values: Mapping[str, str], timezone: str) -> dict[str, str]:
    errors = {}
    parsed = {}
    for key, value in values.items():
        if not value.strip():
            continue
        if key in {"confidence_min", "confidence_max"}:
            parsed[key] = confidence_value(value)
            if parsed[key] is None:
                errors[key] = "0 이상 1 이하의 유한한 숫자를 입력하세요."
        elif key.endswith(("after", "before")):
            parsed[key] = parse_local_time(value, timezone)
            if parsed[key] is None:
                errors[key] = "올바른 날짜와 시간을 입력하세요."
    for low, high in (
        ("after", "before"), ("created_after", "created_before"),
        ("updated_after", "updated_before"), ("confidence_min", "confidence_max"),
    ):
        if (parsed.get(low) is not None and parsed.get(high) is not None
                and parsed[low] > parsed[high]):
            errors[high] = "끝 값은 시작 값 이상이어야 합니다."
    return errors


def applied_filters(data: Mapping) -> dict[str, str]:
    """Return only conditions used by the read model, without derived duplicates."""
    if data.get("filter_errors"):
        return {}
    values = {str(k): str(v).strip() for k, v in data.get("filters", {}).items() if v}
    if "query" in values:
        values["q"] = values.pop("query")
    for key in ("error", "web_search", "memory_failure", "relationship", "retry", "pending"):
        if values.get(key) not in {"yes", "no"}:
            values.pop(key, None)
    for key in ("confidence_min", "confidence_max"):
        if key in values:
            number = confidence_value(values[key])
            values[key] = format(number, ".15g") if number is not None else ""
    if (values.get("origin_scope_type") == "guild"
            and values.get("origin_realm") == "guild:" + values.get("origin_guild_id", "")):
        values.pop("origin_realm", None)
    if not data.get("schema", {}).get("has_lifecycle", True):
        values.pop("status", None)
    return {k: v for k, v in values.items() if v}


def remove_filter_url(path: str, values: Mapping[str, str], key: str) -> str:
    from urllib.parse import urlencode

    remaining = dict(values)
    remaining.pop(key, None)
    remaining.pop("page", None)
    if key.endswith("scope_type"):
        prefix = key.removesuffix("scope_type")
        for suffix in ("guild_id", "channel_id", "realm", "dm_channel_id"):
            remaining.pop(prefix + suffix, None)
    if key == "origin_guild_id":
        remaining.pop("origin_realm", None)
    query = urlencode(remaining)
    return path + ("?" + query if query else "")

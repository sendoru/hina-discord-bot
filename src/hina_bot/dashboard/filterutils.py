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

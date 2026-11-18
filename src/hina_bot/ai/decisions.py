"""Bounded, stateless Gemini Decisions REST requests for candidate selection."""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from time import perf_counter

import httpx

from .prompts import load_prompt

MODEL = "gemini-decision-1"
URL = "https://generativelanguage.googleapis.com/v1beta/decisions"
MAX_QUESTIONS = 1_000
# Conservative UTF-8 cap on the entire JSON body, including all question instructions.
# This leaves headroom below the API's 262,144-token window without an extra counting call.
MAX_REQUEST_BYTES = 240_000
TIMEOUT_SECONDS = 1.5
MIN_PROBABILITY = 0.65


@dataclass(frozen=True)
class DecisionResult:
    candidate_count: int = 0
    scores: dict[str, float] | None = None
    reason: str = ""
    request_bytes: int = 0
    question_count: int = 0
    input_tokens: int | None = None
    request_ms: int | None = None
    http_status: int | None = None
    elapsed_ms: int = 0

    def metrics(self) -> dict:
        scores = list((self.scores or {}).values())
        return {
            "decision_candidates": self.candidate_count,
            "decision_questions": self.question_count,
            "decision_request_bytes": self.request_bytes,
            "decision_request_ms": self.request_ms,
            "decision_elapsed_ms": self.elapsed_ms,
            "decision_http_status": self.http_status,
            "decision_reason": self.reason,
            "decision_input_tokens": self.input_tokens,
            "decision_score_bands": {
                "below_half": sum(score < 0.5 for score in scores),
                "below_threshold": sum(0.5 <= score < MIN_PROBABILITY for score in scores),
                "eligible": sum(score >= MIN_PROBABILITY for score in scores),
            },
        }


class InvalidDecisionResponse(ValueError):
    pass


def _unique_keys(pairs: list[tuple]) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            raise InvalidDecisionResponse("duplicate_response_key")
        result[key] = value
    return result


def _parse_scores(response: dict, keys: set[str]) -> dict[str, float]:
    answers = response.get("answers")
    if not isinstance(answers, dict):
        raise InvalidDecisionResponse("invalid_answers")
    if keys - answers.keys():
        raise InvalidDecisionResponse("missing_answers")
    if answers.keys() - keys:
        raise InvalidDecisionResponse("unexpected_answers")
    scores = {}
    for key, answer in answers.items():
        if not isinstance(answer, dict) or answer.get("type") != "noul":
            raise InvalidDecisionResponse("invalid_answer_type")
        probability = answer.get("noul")
        if type(probability) not in {int, float} or not 0 <= probability <= 1:
            raise InvalidDecisionResponse("invalid_probability")
        scores[key] = float(probability)
    return scores


def _input_tokens(response: dict) -> int | None:
    usage = response.get("usage")
    tokens = usage.get("total_input_tokens") if isinstance(usage, dict) else None
    return tokens if type(tokens) is int and tokens >= 0 else None


@dataclass
class DecisionsClient:
    http: httpx.AsyncClient | None = None

    async def close(self) -> None:
        if self.http is not None:
            await self.http.aclose()

    async def evaluate(
        self, *, api_key: str, domain: str, query: dict, candidates: dict[str, dict],
    ) -> DecisionResult:
        started = perf_counter()
        count = len(candidates)

        def result(reason="", **kwargs):
            return DecisionResult(
                candidate_count=count, reason=reason,
                elapsed_ms=round((perf_counter() - started) * 1000), **kwargs,
            )

        if not count:
            return result("no_candidates", scores={})
        if count > MAX_QUESTIONS:
            return result("question_limit")
        if not api_key:
            return result("missing_api_key")

        question = load_prompt("decisions_question.md").strip()
        # Wire keys are positional and short. Stable source IDs stay in the local mapping,
        # including numeric memory IDs and lore IDs containing periods.
        ids = {f"c{index}": item_id for index, item_id in enumerate(sorted(candidates), 1)}
        shared = {
            "selection_rules": load_prompt(f"decisions_{domain}.md").strip(),
            "turn": query,
            "candidates": {key: candidates[item_id] for key, item_id in ids.items()},
        }
        payload = {
            "model": MODEL,
            "input": json.dumps(shared, ensure_ascii=False, separators=(",", ":")),
            "questions": {
                key: {"type": "noul", "instructions": question.format(candidate=key)}
                for key in ids
            },
        }
        body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode()
        request_meta = {"request_bytes": len(body), "question_count": count}
        if len(body) > MAX_REQUEST_BYTES:
            return result("request_size_limit", **request_meta)

        if self.http is None:
            self.http = httpx.AsyncClient()
        request_started = perf_counter()
        try:
            async with asyncio.timeout(TIMEOUT_SECONDS):
                response = await self.http.post(
                    URL, content=body,
                    headers={"x-goog-api-key": api_key, "content-type": "application/json"},
                )
        except (TimeoutError, httpx.TimeoutException):
            reason = "timeout"
        except httpx.RequestError:
            reason = "transport_error"
        else:
            request_meta["http_status"] = response.status_code
            reason = ""
        request_meta["request_ms"] = round((perf_counter() - request_started) * 1000)
        if reason:
            return result(reason, **request_meta)
        if response.status_code != 200:
            if response.status_code == 429:
                reason = "rate_limited"
            elif response.status_code >= 500:
                reason = "provider_error"
            else:
                reason = "http_error"
            return result(reason, **request_meta)

        try:
            data = response.json(object_pairs_hook=_unique_keys)
        except InvalidDecisionResponse as exc:
            return result(str(exc), **request_meta)
        except (ValueError, RecursionError):
            return result("invalid_json", **request_meta)
        if not isinstance(data, dict):
            return result("invalid_response", **request_meta)
        request_meta["input_tokens"] = _input_tokens(data)
        try:
            scores = _parse_scores(data, set(ids))
        except InvalidDecisionResponse as exc:
            return result(str(exc), **request_meta)
        return result(scores={ids[key]: score for key, score in scores.items()}, **request_meta)

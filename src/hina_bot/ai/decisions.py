"""Bounded, stateless native Decisions REST requests for candidate selection."""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from time import perf_counter

import httpx

from .pdf_transport import (
    DecisionCandidateReference,
    consolidate_references,
    encode_reference,
    transport_coverage,
)
from .prompts import load_prompt

MODEL = "gemini-decision-1"
URL = "https://generativelanguage.googleapis.com/v1beta/decisions"
MAX_QUESTIONS = 1_000
OPENAI_MODEL = "gpt-6-luna"
OPENAI_URL = "https://api.openai.com/v1/decisions"
OPENAI_MAX_QUESTIONS = 400
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
    transport: dict | None = None
    output_tokens: int | None = None
    cached_tokens: int | None = None
    reasoning_tokens: int | None = None
    request_count: int = 0
    cache_write_tokens: int | None = None

    def metrics(self) -> dict:
        scores = list((self.scores or {}).values())
        return {
            "decision_candidates": self.candidate_count,
            "decision_transport": self.transport or {"transport": "text"},
            "decision_questions": self.question_count,
            "decision_request_bytes": self.request_bytes,
            "decision_request_ms": self.request_ms,
            "decision_elapsed_ms": self.elapsed_ms,
            "decision_http_status": self.http_status,
            "decision_reason": self.reason,
            "decision_input_tokens": self.input_tokens,
            "decision_output_tokens": self.output_tokens,
            "decision_cached_tokens": self.cached_tokens,
            "decision_reasoning_tokens": self.reasoning_tokens,
            "decision_request_count": self.request_count or int(self.request_ms is not None),
            "decision_cache_write_tokens": self.cache_write_tokens,
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


def _usage_count(usage, key):
    value = usage.get(key) if isinstance(usage, dict) else None
    return value if type(value) is int and value >= 0 else None


def _input_tokens(response: dict) -> int | None:
    usage = response.get("usage")
    tokens = usage.get("total_input_tokens") if isinstance(usage, dict) else None
    return tokens if type(tokens) is int and tokens >= 0 else None



def _openai_payload(shared, ids, question):
    return {
        "model": OPENAI_MODEL,
        "input": _json(shared),
        "questions": [
            {"type": "predicate", "name": key, "instructions": question.format(candidate=key)}
            for key in ids
        ],
    }


def _openai_scores(data, keys):
    answers = data.get("answers")
    if not isinstance(answers, list):
        raise InvalidDecisionResponse("invalid_answers")
    scores = {}
    for answer in answers:
        if not isinstance(answer, dict) or answer.get("type") != "predicate":
            raise InvalidDecisionResponse("invalid_answer_type")
        key = answer.get("name")
        if not isinstance(key, str) or key not in keys:
            raise InvalidDecisionResponse("unexpected_answers")
        if key in scores:
            raise InvalidDecisionResponse("duplicate_response_key")
        probability = answer.get("probability")
        if type(probability) not in {int, float} or not 0 <= probability <= 1:
            raise InvalidDecisionResponse("invalid_probability")
        scores[key] = float(probability)
    if scores.keys() != keys:
        raise InvalidDecisionResponse("missing_answers")
    return scores


PDF_PREP_TIMEOUT = 2.0


def _json(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


async def _candidate_pdf_input(shared, *, deadline_seconds=None):
    control = {key: value for key, value in shared.items() if key != "candidates"}
    blocks = [{
        "type": "text",
        "text": _json(control) + "\nCandidate records follow in ordered JSON blocks. "
        "All blocks belong to the same candidates map; use the exact candidate IDs.\n",
    }]
    # Try the complete map first; if it cannot be represented, split by intact
    # candidate records and render only the successful pieces into one document.
    whole = DecisionCandidateReference(type="input_text", text=_json({"candidates": shared["candidates"]}))
    prepared = await consolidate_references([whole], encoder=encode_reference, deadline_seconds=deadline_seconds)
    observations = []
    for part in prepared:
        for fragment, document, obs in part.segments:
            observations.append({**obs, "section": "decision_candidates"})
            if document is not None:
                blocks.append({"type": "document", "mime_type": "application/pdf", "data": document.data})
            elif fragment is not None:
                blocks.append({"type": "text", "text": fragment["text"] + "\n"})
    return blocks, observations


async def _prepare_decision_body(payload, shared, original_body, deadline):
    """Pre-send opt-in only; never make a second provider call."""
    started = perf_counter()
    # The planner owns the deadline so a verified partial document can survive
    # expiry. Keep the existing separate HTTP timeout for the actual Decision.
    blocks, observations = await _candidate_pdf_input(
        shared, deadline_seconds=min(PDF_PREP_TIMEOUT, deadline),
    )
    coverage = transport_coverage(observations)
    meta = {
        **coverage,
        "transport": "pdf" if coverage["pages"] else "text",
        "fallback": "" if coverage["pages"] else next(
            (obs["fallback"] for obs in observations if obs["fallback"]), "no_eligible_document",
        ),
        "encode_ms": round((perf_counter() - started) * 1000),
        "reference_transport": observations,
    }
    if not coverage["pages"]:
        return original_body, meta
    body = _json({**payload, "input": blocks}).encode()
    if len(body) > MAX_REQUEST_BYTES:
        meta.update(
            transport="text", fallback="request_size_limit",
            attempted_pages=meta["pages"], attempted_pdf_bytes=meta["pdf_bytes"],
            documents=0, pages=0, pdf_bytes=0, pdf_chars=0, eligible_coverage=0.0,
            total_coverage=0.0,
        )
        for item in observations:
            if item["transport"] == "pdf":
                item.update(
                    transport="text", fallback="request_size_limit",
                    attempted_pages=item["pages"], attempted_pdf_bytes=item["pdf_bytes"],
                    documents=0, pages=0, pdf_bytes=0,
                )
        return original_body, meta
    return body, meta


@dataclass
class DecisionsClient:
    http: httpx.AsyncClient | None = None
    provider: str = "gemini"
    model: str = MODEL

    async def close(self) -> None:
        if self.http is not None:
            await self.http.aclose()

    async def evaluate(
        self, *, api_key: str, domain: str, query: dict, candidates: dict[str, dict],
        timeout_seconds: float | None = None,
        pdf_transport: bool = False,
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
        if self.provider not in {"openai", "gemini"}:
            return result("unsupported_provider")
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
        if self.provider == "openai":
            return await self._evaluate_openai(
                api_key, shared, ids, question, started,
                TIMEOUT_SECONDS if timeout_seconds is None else timeout_seconds,
            )
        payload = {
            "model": MODEL,
            "input": json.dumps(shared, ensure_ascii=False, separators=(",", ":")),
            "questions": {
                key: {"type": "noul", "instructions": question.format(candidate=key)}
                for key in ids
            },
        }
        url = URL
        headers = {"x-goog-api-key": api_key}
        body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode()
        request_meta = {"request_bytes": len(body), "question_count": count}
        if len(body) > MAX_REQUEST_BYTES:
            return result("request_size_limit", **request_meta)

        deadline = TIMEOUT_SECONDS if timeout_seconds is None else timeout_seconds
        if pdf_transport and self.provider == "gemini":
            body, transport = await _prepare_decision_body(payload, shared, body, deadline)
            request_meta.update(request_bytes=len(body), transport=transport)
        if self.http is None:
            self.http = httpx.AsyncClient()
        request_started = perf_counter()
        try:
            async with asyncio.timeout(deadline):
                response = await self.http.post(
                    url, content=body,
                    headers={**headers, "content-type": "application/json"},
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
        except (ValueError, TypeError, KeyError, AttributeError, RecursionError):
            return result("invalid_response", **request_meta)
        return result(scores={ids[key]: score for key, score in scores.items()}, **request_meta)

    async def _evaluate_openai(self, api_key, shared, ids, question, started, deadline):
        """Partition independent predicates; use one deadline and fail closed on any batch."""
        keys = list(ids)
        batches = []
        for offset in range(0, len(keys), OPENAI_MAX_QUESTIONS):
            batch = keys[offset:offset + OPENAI_MAX_QUESTIONS]
            batch_shared = {
                **shared, "candidates": {key: shared["candidates"][key] for key in batch},
            }
            body = _json(_openai_payload(batch_shared, batch, question)).encode()
            batches.append((batch, body))
        total_bytes = sum(len(body) for _, body in batches)

        def result(reason="", **kwargs):
            return DecisionResult(
                candidate_count=len(ids), reason=reason, question_count=len(ids),
                request_bytes=total_bytes,
                elapsed_ms=round((perf_counter() - started) * 1000), **kwargs,
            )

        # Bound aggregate payload, not just the individual requests.
        if total_bytes > MAX_REQUEST_BYTES:
            return result("request_size_limit")
        if self.http is None:
            self.http = httpx.AsyncClient()
        scores = {}
        usage_totals = {}
        request_count = 0
        request_started = perf_counter()
        status = None
        reason = ""
        try:
            async with asyncio.timeout(max(0, deadline - (perf_counter() - started))):
                for batch, body in batches:
                    request_count += 1
                    status = None
                    response = await self.http.post(
                        OPENAI_URL, content=body,
                        headers={"Authorization": f"Bearer {api_key}", "content-type": "application/json"},
                    )
                    status = response.status_code
                    if status != 200:
                        reason = ("rate_limited" if status == 429 else
                                  "provider_error" if status >= 500 else "http_error")
                        break
                    data = response.json(object_pairs_hook=_unique_keys)
                    if not isinstance(data, dict):
                        raise InvalidDecisionResponse("invalid_response")
                    usage = data.get("usage")
                    input_details = usage.get("input_tokens_details") if isinstance(usage, dict) else None
                    output_details = usage.get("output_tokens_details") if isinstance(usage, dict) else None
                    for field, value in {
                        "input_tokens": _usage_count(usage, "input_tokens"),
                        "output_tokens": _usage_count(usage, "output_tokens"),
                        "cached_tokens": _usage_count(input_details, "cached_tokens"),
                        "cache_write_tokens": _usage_count(input_details, "cache_write_tokens"),
                        "reasoning_tokens": _usage_count(output_details, "reasoning_tokens"),
                    }.items():
                        if value is not None:
                            usage_totals[field] = usage_totals.get(field, 0) + value
                    scores.update(_openai_scores(data, set(batch)))
        except (TimeoutError, httpx.TimeoutException):
            reason = "timeout"
        except httpx.RequestError:
            reason = "transport_error"
        except InvalidDecisionResponse as exc:
            reason = str(exc)
        except (ValueError, TypeError, KeyError, AttributeError, RecursionError):
            reason = "invalid_response"
        return result(
            reason, scores=None if reason else {ids[key]: score for key, score in scores.items()},
            request_count=request_count, http_status=status,
            request_ms=round((perf_counter() - request_started) * 1000) if request_count else None,
            **usage_totals,
        )

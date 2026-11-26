"""Bounded, stateless Gemini Decisions REST requests for candidate selection."""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from time import perf_counter

import httpx

from .pdf_transport import consolidate_references, encode_reference, transport_coverage
from .reference_context_blocks import PDFReferenceText
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
    transport: dict | None = None

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



PDF_PREP_TIMEOUT = 0.25


def _json(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


async def _candidate_pdf_input(shared):
    control = {key: value for key, value in shared.items() if key != "candidates"}
    blocks = [{
        "type": "text",
        "text": _json(control) + "\nCandidate records follow in ordered JSON blocks. "
        "All blocks belong to the same candidates map; use the exact candidate IDs.\n",
    }]
    # Try the complete map first; if it cannot be represented, split by intact
    # candidate records and render only the successful pieces into one document.
    candidate_map = shared["candidates"]
    whole = PDFReferenceText(type="input_text", text=_json({"candidates": candidate_map}))
    prepared = await consolidate_references([whole], encoder=encode_reference)
    if not prepared[0].document and len(candidate_map) > 1:
        batches = []
        batch = {}
        for key, candidate in candidate_map.items():
            batch[key] = candidate
            if len(batch) >= 32:
                batches.append(batch)
                batch = {}
        if batch:
            batches.append(batch)
        if len(batches) == 1:
            middle = len(candidate_map) // 2
            records = list(candidate_map.items())
            batches = [dict(records[:middle]), dict(records[middle:])]
        prepared = await consolidate_references([
            PDFReferenceText(type="input_text", text=_json({"candidates": batch}))
            for batch in batches
        ], encoder=encode_reference)
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
    try:
        async with asyncio.timeout(min(PDF_PREP_TIMEOUT, deadline / 4)):
            blocks, observations = await _candidate_pdf_input(shared)
    except TimeoutError:
        return original_body, {
            "transport": "text", "fallback": "encode_deadline",
            "encode_ms": round((perf_counter() - started) * 1000),
        }
    coverage = transport_coverage(observations)
    meta = {
        **coverage,
        "transport": "pdf" if coverage["pages"] else "text",
        "fallback": "" if coverage["pages"] else "no_eligible_document",
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

        deadline = TIMEOUT_SECONDS if timeout_seconds is None else timeout_seconds
        if pdf_transport:
            body, transport = await _prepare_decision_body(payload, shared, body, deadline)
            request_meta.update(request_bytes=len(body), transport=transport)
        if self.http is None:
            self.http = httpx.AsyncClient()
        request_started = perf_counter()
        try:
            async with asyncio.timeout(deadline):
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

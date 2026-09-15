import json
import re
from dataclasses import dataclass
from importlib.resources import files
from pathlib import Path

from .character import get_character_config

LANES = {"canon", "community_meme"}
KNOWLEDGE_LEVELS = {
    "self", "direct_experience", "reported", "public_knowledge", "inference",
    "audience_only", "unknown",
}
CONFIDENCE_LEVELS = {"verified", "official_secondary", "crosschecked", "candidate"}
FACT_TYPES = {
    "fact_direct", "fact_visual", "fact_reported", "inference", "unknown",
    "adaptation", "fandom",
}
FACT_TYPE_TO_KIND = {
    "fact_direct": "world_fact",
    "fact_visual": "world_fact",
    "fact_reported": "world_fact",
    "inference": "interpretation",
    "unknown": "interpretation",
    "adaptation": "interpretation",
    "fandom": "interpretation",
}
REFERENCE_ONLY_FACT_TYPES = {"adaptation", "fandom"}
_TOKEN = re.compile(r"[0-9A-Za-z가-힣]{2,}")
_BASE_STOPWORDS = {"뭐야", "알려줘", "어떻게"}


def _stopwords() -> set[str]:
    return _BASE_STOPWORDS | set(get_character_config().search_stopwords)


class LoreValidationError(ValueError):
    pass


def fact_type(record: dict) -> str:
    """Return the evidence class, preserving compatibility with old reviewed rows."""
    value = record.get("fact_type")
    if value is not None:
        return value
    if record.get("lane") == "community_meme":
        return "fandom"
    return "fact_direct"


def validate_record(record: dict, *, accepted: bool = False) -> dict:
    required = {"id", "lane", "summary", "keywords", "subjects", "knowledge", "confidence",
                "source", "status", "kr_release", "timeline"}
    missing = required - record.keys()
    if missing:
        raise LoreValidationError(f"{record.get('id', '<unknown>')}: missing {sorted(missing)}")
    if not isinstance(record["id"], str) or not re.fullmatch(r"[a-z0-9_.-]{3,100}", record["id"]):
        raise LoreValidationError("id는 영문 소문자·숫자·._- 형식이어야 합니다.")
    if record["lane"] not in LANES:
        raise LoreValidationError(f"invalid lane: {record['lane']}")
    if record.get("fact_type") is not None and record["fact_type"] not in FACT_TYPES:
        raise LoreValidationError(f"invalid fact_type: {record['fact_type']}")
    if record["knowledge"] not in KNOWLEDGE_LEVELS:
        raise LoreValidationError(f"invalid knowledge: {record['knowledge']}")
    if record["confidence"] not in CONFIDENCE_LEVELS:
        raise LoreValidationError(f"invalid confidence: {record['confidence']}")
    if record["status"] not in {"candidate", "accepted", "rejected", "suppressed"}:
        raise LoreValidationError(f"invalid status: {record['status']}")
    if record["kr_release"] not in {"confirmed", "pending", "not_applicable"}:
        raise LoreValidationError(f"invalid kr_release: {record['kr_release']}")
    if record["lane"] == "canon" and accepted and record["kr_release"] != "confirmed":
        raise LoreValidationError(f"{record['id']}: Korean release must be confirmed")
    if record["lane"] == "community_meme" and record["kr_release"] != "not_applicable":
        raise LoreValidationError(f"{record['id']}: meme release status must be not_applicable")
    if accepted and (record["status"] != "accepted" or record["confidence"] == "candidate"):
        raise LoreValidationError(f"{record['id']}: runtime records must be reviewed and accepted")
    if accepted and record["lane"] == "canon" and fact_type(record) in REFERENCE_ONLY_FACT_TYPES:
        raise LoreValidationError(f"{record['id']}: adaptation/fandom rows are reference-only")
    if not isinstance(record["summary"], str) or not 1 <= len(record["summary"].strip()) <= 600:
        raise LoreValidationError(f"{record['id']}: summary must be 1~600 chars")
    if not isinstance(record["timeline"], str) or not 1 <= len(record["timeline"].strip()) <= 120:
        raise LoreValidationError(f"{record['id']}: timeline must be 1~120 chars")
    for key in ("keywords", "subjects"):
        values = record[key]
        if not isinstance(values, list) or not values or len(values) > 30:
            raise LoreValidationError(f"{record['id']}: {key} must be a non-empty list")
        if any(not isinstance(value, str) or not value.strip() or len(value) > 60 for value in values):
            raise LoreValidationError(f"{record['id']}: invalid {key}")
    source = record["source"]
    if not isinstance(source, dict) or not {"type", "title", "locator"} <= source.keys():
        raise LoreValidationError(f"{record['id']}: incomplete source")
    if record["lane"] == "community_meme" and not record.get("reaction"):
        raise LoreValidationError(f"{record['id']}: community meme needs a reaction guide")
    return record


def read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    rows = []
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if line.strip():
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError as exc:
                raise LoreValidationError(f"{path}:{number}: {exc}") from exc
    return rows


def write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n"
                            for row in rows), encoding="utf-8")


@dataclass
class LoreIndex:
    records: list[dict]

    @classmethod
    def load(cls, path: str = "") -> "LoreIndex":
        target = Path(path) if path else files("hina_bot").joinpath("data/lore.jsonl")
        records = [validate_record(row, accepted=True) for row in read_jsonl(Path(target))]
        ids = [row["id"] for row in records]
        if len(ids) != len(set(ids)):
            raise LoreValidationError("runtime lore contains duplicate ids")
        return cls(records)

    @staticmethod
    def _terms(text: str) -> set[str]:
        stopwords = _stopwords()
        return {token.casefold() for token in _TOKEN.findall(text) if token.casefold() not in stopwords}

    def search(self, query: str, *, limit: int = 6, chars: int = 3200,
               include_community: bool = True, include_reference_only: bool = False) -> list[dict]:
        if limit <= 0 or chars <= 0:
            return []
        folded = query.casefold()
        terms = self._terms(query)
        stopwords = _stopwords()
        ranked = []
        for order, record in enumerate(self.records):
            if record["lane"] == "community_meme" and not include_community:
                continue
            evidence_type = fact_type(record)
            if record["lane"] == "canon" and evidence_type in REFERENCE_ONLY_FACT_TYPES \
                    and not include_reference_only:
                continue
            score = 0
            for value in record["subjects"]:
                value = value.casefold()
                if value not in stopwords and value in folded:
                    score += 8 + min(len(value), 8)
            for value in record["keywords"]:
                value = value.casefold()
                if value in folded:
                    score += 5 + min(len(value), 8)
            score += 2 * len(terms & self._terms(record["summary"]))
            if score:
                ranked.append((score, -order, record))
        result, used = [], 0
        for _, _, record in sorted(ranked, reverse=True):
            if record["lane"] == "community_meme":
                # The model needs the reaction, not editorial provenance that it may say aloud.
                item = {"kind": "optional_reaction", "content": record["reaction"]}
            else:
                evidence_type = fact_type(record)
                item = {
                    "reference": record["id"], "kind": FACT_TYPE_TO_KIND[evidence_type],
                    "content": record["summary"], "awareness": record["knowledge"],
                    "time": record["timeline"],
                }
                if evidence_type == "unknown":
                    # Existing prompt policy already treats interpretations as non-facts; this flag makes
                    # the negative/unknown claim explicit without inventing a new model-facing kind.
                    item["guard"] = "do_not_assert_positive_fact"
                elif evidence_type in REFERENCE_ONLY_FACT_TYPES:
                    item["source_scope"] = evidence_type
            size = len(json.dumps(item, ensure_ascii=False))
            if used + size > chars:
                continue
            result.append(item)
            used += size
            if len(result) >= limit:
                break
        return result

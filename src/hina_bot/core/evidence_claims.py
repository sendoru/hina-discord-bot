"""Typed proposition metadata for local-evidence sufficiency."""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum


class EvidencePolarity(StrEnum):
    POSITIVE = "positive"
    NEGATIVE = "negative"
    UNKNOWN = "unknown"


_EVIDENCE_PREDICATES = {
    "addressing",
    "affiliation",
    "awareness",
    "direct_meeting",
    "relationship_state",
    "role",
    "school_year",
    "school_year_relation",
}
_SYMMETRIC_PREDICATES = {
    "direct_meeting",
    "relationship_state",
    "school_year_relation",
}
_CANONICAL_ENTITY = re.compile(r"character\.[a-z0-9_.-]+")
_SLUG = re.compile(r"[a-z0-9][a-z0-9_.-]{0,99}")


@dataclass(frozen=True)
class EvidenceClaim:
    """One reviewed proposition a knowledge row can support.

    subject/object are canonical entities. time_scope is a normalized semantic
    qualifier, not display prose. value is the known answer value where useful.
    """

    predicate: str
    subject: str
    object: str | None = None
    value: str | None = None
    time_scope: str | None = None
    polarity: EvidencePolarity = EvidencePolarity.POSITIVE

    def __post_init__(self) -> None:
        if self.predicate not in _EVIDENCE_PREDICATES:
            raise ValueError("unsupported evidence predicate")
        if not _CANONICAL_ENTITY.fullmatch(self.subject):
            raise ValueError("evidence claim subject must be a canonical character entity")
        if self.object is not None and not _CANONICAL_ENTITY.fullmatch(self.object):
            raise ValueError("evidence claim object must be a canonical character entity")
        if self.object == self.subject:
            raise ValueError("evidence claim subject/object must be distinct")
        if self.time_scope is not None and not _SLUG.fullmatch(self.time_scope):
            raise ValueError("invalid evidence time scope")
        if self.value is not None and (
            not isinstance(self.value, str)
            or not 1 <= len(self.value.strip()) <= 80
        ):
            raise ValueError("invalid evidence claim value")

    @property
    def symmetric(self) -> bool:
        return self.predicate in _SYMMETRIC_PREDICATES

    def entity_set(self) -> frozenset[str]:
        return frozenset(
            entity for entity in (self.subject, self.object) if entity is not None
        )


def evidence_claim_from_mapping(raw: object) -> EvidenceClaim:
    if not isinstance(raw, dict):
        raise TypeError("evidence claim must be an object")
    allowed = {"predicate", "subject", "object", "value", "time_scope", "polarity"}
    if set(raw) - allowed:
        raise ValueError("unknown evidence claim field")
    try:
        polarity = EvidencePolarity(raw.get("polarity", EvidencePolarity.POSITIVE))
        return EvidenceClaim(
            predicate=raw["predicate"],
            subject=raw["subject"],
            object=raw.get("object"),
            value=raw.get("value"),
            time_scope=raw.get("time_scope"),
            polarity=polarity,
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("invalid evidence claim") from exc


def evidence_claim_to_mapping(claim: EvidenceClaim) -> dict:
    row = {
        "predicate": claim.predicate,
        "subject": claim.subject,
        "polarity": claim.polarity.value,
    }
    if claim.object is not None:
        row["object"] = claim.object
    if claim.value is not None:
        row["value"] = claim.value
    if claim.time_scope is not None:
        row["time_scope"] = claim.time_scope
    return row

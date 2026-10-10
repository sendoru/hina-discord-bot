"""Structured local-evidence sufficiency for Retrieval v2 and web fallback."""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass

from .entity_resolution import HINA_ENTITY_ID
from .evidence_claims import EvidenceClaim, EvidencePolarity
from .knowledge_retrieval import (
    KnowledgeCandidate,
    KnowledgeUsage,
    RankedKnowledgeCandidate,
)
from .retrieval_v2 import KnowledgeBundle, RetrievalIntent, RetrievalRequest

_TRUSTED_CONFIDENCE = {"verified", "official_secondary", "crosschecked"}
_DIRECT_FACT_TYPES = {"fact_direct", "fact_visual", "fact_reported"}
_SYMMETRIC = {"direct_meeting", "relationship_state", "school_year_relation"}
_STABLE_VALUE_PREDICATES = {"affiliation", "role", "school_year", "school_year_relation"}

_BEFORE_MEETING = re.compile(r"(?:만나|대면).{0,12}(?:기\s*)?(?:전|이전|전부터)|(?:전|이전|전부터).{0,12}(?:만나|대면)")
_AWARENESS = re.compile(r"(?:알고\s*있|알았|인지|파악|주시|이력|과거를\s*알)")
_DIRECT_MEETING = re.compile(r"(?:직접.{0,8}만나|만나본|만난\s*적|첫\s*만남|처음.{0,8}만나)")
_SCHOOL_RELATION = re.compile(r"(?:선배|후배|동급생|같은\s*학년)")
_ADDRESSING = re.compile(r"(?:호칭|뭐라고\s*부르|어떻게\s*부르|이름으로\s*부르)")
_RELATIONSHIP = re.compile(r"(?:무슨\s*사이|어떤\s*관계|친분|친했|가까운\s*사이)")
_CURRENT = re.compile(r"(?:지금|현재|요즘)")


@dataclass(frozen=True)
class EvidenceRequirement:
    predicate: str
    subject: str | None = None
    object: str | None = None
    time_scope: str | None = None

    @property
    def symmetric(self) -> bool:
        return self.predicate in _SYMMETRIC


@dataclass(frozen=True)
class EvidenceAssessment:
    """Content-free sufficiency result safe for routing/telemetry."""

    sufficient: bool
    reason: str
    matched_ids: tuple[str, ...] = ()
    predicate: str | None = None
    answer_state: str = "missing"


def _entity_pair(
    request: RetrievalRequest,
    *,
    rp_entity: str,
) -> tuple[str, str] | None:
    if request.relation_pair is not None:
        return request.relation_pair
    entities = list(request.entities)
    if len(entities) >= 2:
        return entities[0], entities[1]
    if len(entities) == 1 and entities[0] != rp_entity:
        return rp_entity, entities[0]
    return None


def evidence_requirement(
    request: RetrievalRequest,
    *,
    rp_entity: str = HINA_ENTITY_ID,
) -> EvidenceRequirement | None:
    """Extract only proposition facets whose evidence semantics are explicit.

    This parser reads the question, never candidate summaries. Unrecognized relation/event
    wording remains unresolved so web fallback stays conservative.
    """
    text = " ".join((request.anchor_text + " " + request.visible_text).split())
    pair = _entity_pair(request, rp_entity=rp_entity)

    if _BEFORE_MEETING.search(text) and _AWARENESS.search(text):
        if len(request.entities) >= 2:
            subject, obj = request.entities[0], request.entities[1]
        elif pair is not None:
            subject, obj = rp_entity, next(entity for entity in pair if entity != rp_entity)
        else:
            subject = obj = None
        return EvidenceRequirement("awareness", subject, obj, "before_first_meeting")

    if _SCHOOL_RELATION.search(text):
        subject, obj = pair if pair is not None else (None, None)
        return EvidenceRequirement("school_year_relation", subject, obj, "profile")

    if _ADDRESSING.search(text):
        if len(request.entities) >= 2:
            subject, obj = request.entities[0], request.entities[1]
        elif pair is not None:
            other = next(entity for entity in pair if entity != rp_entity)
            subject, obj = other, rp_entity
        else:
            subject = obj = None
        scope = "current" if _CURRENT.search(text) else None
        return EvidenceRequirement("addressing", subject, obj, scope)

    if _DIRECT_MEETING.search(text):
        subject, obj = pair if pair is not None else (None, None)
        scope = "first_meeting" if re.search(r"(?:첫\s*만남|처음)", text) else None
        return EvidenceRequirement("direct_meeting", subject, obj, scope)

    if _RELATIONSHIP.search(text):
        subject, obj = pair if pair is not None else (None, None)
        scope = "current" if _CURRENT.search(text) else None
        return EvidenceRequirement("relationship_state", subject, obj, scope)

    if _AWARENESS.search(text) and pair is not None:
        if len(request.entities) >= 2:
            subject, obj = request.entities[0], request.entities[1]
        else:
            subject, obj = rp_entity, next(entity for entity in pair if entity != rp_entity)
        return EvidenceRequirement("awareness", subject, obj)

    return None


def _reviewed(candidate: KnowledgeCandidate) -> bool:
    return (
        candidate.lane == "canon"
        and candidate.confidence in _TRUSTED_CONFIDENCE
        and candidate.kr_release == "confirmed"
        and bool(candidate.source_metadata)
    )


def _evidence_quality(
    candidate: KnowledgeCandidate,
    index: dict[str, KnowledgeCandidate],
    seen: frozenset[str] = frozenset(),
) -> str | None:
    if not _reviewed(candidate):
        return None
    if candidate.fact_type in _DIRECT_FACT_TYPES:
        return "direct"
    if candidate.fact_type == "unknown":
        return "unknown"
    if candidate.fact_type != "inference" or not candidate.evidence_ids:
        return None
    if candidate.candidate_id in seen:
        return None
    chain = seen | {candidate.candidate_id}
    supports = [index.get(identifier) for identifier in candidate.evidence_ids]
    if any(row is None for row in supports):
        return None
    qualities = [_evidence_quality(row, index, chain) for row in supports if row is not None]
    if qualities and all(quality in {"direct", "derived"} for quality in qualities):
        return "derived"
    return None


def _entities_compatible(
    candidate: KnowledgeCandidate,
    request: RetrievalRequest,
) -> bool:
    if not candidate.entities or not request.entities:
        return True
    return bool(set(candidate.entities) & set(request.entities))


def _claim_matches(claim: EvidenceClaim, requirement: EvidenceRequirement) -> bool:
    if claim.predicate != requirement.predicate:
        return False
    if requirement.subject is None or requirement.object is None:
        return False
    if requirement.symmetric:
        if claim.entity_set() != frozenset((requirement.subject, requirement.object)):
            return False
    elif claim.subject != requirement.subject or claim.object != requirement.object:
        return False
    return not (
        requirement.time_scope is not None
        and claim.time_scope != requirement.time_scope
    )


def _conflicting_claims(claims: Iterable[EvidenceClaim], predicate: str) -> bool:
    rows = tuple(claims)
    polarities = {claim.polarity for claim in rows}
    if EvidencePolarity.UNKNOWN in polarities and len(polarities) > 1:
        return True
    if EvidencePolarity.POSITIVE in polarities and EvidencePolarity.NEGATIVE in polarities:
        return True
    if predicate in _STABLE_VALUE_PREDICATES:
        values = {claim.value for claim in rows if claim.polarity == EvidencePolarity.POSITIVE}
        if len(values) > 1:
            return True
    return False


def selected_evidence_bundle(
    candidates: Iterable[KnowledgeCandidate],
) -> KnowledgeBundle:
    """Adapt already-selected legacy/local candidates to the v2 evidence contract."""
    rows = tuple(
        RankedKnowledgeCandidate(1.0, order, candidate)
        for order, candidate in enumerate(candidates)
    )
    return KnowledgeBundle(
        facts=tuple(
            row for row in rows
            if KnowledgeUsage.FACTUAL in row.candidate.retrieval_usages
        ),
        relations=tuple(
            row for row in rows
            if KnowledgeUsage.RELATION in row.candidate.retrieval_usages
        ),
    )


def assess_local_evidence(
    request: RetrievalRequest,
    bundle: KnowledgeBundle,
    *,
    supporting_candidates: Iterable[KnowledgeCandidate] = (),
    rp_entity: str = HINA_ENTITY_ID,
) -> EvidenceAssessment:
    """Decide whether selected local evidence can answer the current proposition.

    Ambient/reaction rows never participate. Retrieval relevance is assumed to have been
    admitted upstream; this layer checks provenance, proposition coverage and evidence
    quality. Missing metadata fails closed to web fallback rather than guessing.
    """
    rows = tuple(bundle.relations) + tuple(bundle.facts)
    index = {candidate.candidate_id: candidate for candidate in supporting_candidates}
    index.update((row.candidate.candidate_id, row.candidate) for row in rows)
    requirement = evidence_requirement(request, rp_entity=rp_entity)

    if requirement is not None:
        if requirement.subject is None or requirement.object is None:
            return EvidenceAssessment(
                False, "entity_context_missing", predicate=requirement.predicate
            )
        matches: list[tuple[RankedKnowledgeCandidate, EvidenceClaim, str]] = []
        for row in rows:
            quality = _evidence_quality(row.candidate, index)
            if quality is None:
                continue
            for claim in row.candidate.claims:
                if _claim_matches(claim, requirement):
                    matches.append((row, claim, quality))
        if not matches:
            return EvidenceAssessment(
                False, "proposition_not_covered", predicate=requirement.predicate
            )
        claims = [claim for _, claim, _ in matches]
        if _conflicting_claims(claims, requirement.predicate):
            return EvidenceAssessment(
                False,
                "conflicting_local_evidence",
                tuple(dict.fromkeys(row.candidate.candidate_id for row, _, _ in matches)),
                requirement.predicate,
                "conflict",
            )
        qualities = {quality for _, _, quality in matches}
        polarities = {claim.polarity for claim in claims}
        if polarities == {EvidencePolarity.UNKNOWN}:
            answer_state = "unknown"
            reason = "matched_explicit_unknown"
        elif "direct" in qualities:
            answer_state = "supported"
            reason = "matched_direct_claim"
        else:
            answer_state = "derived"
            reason = "matched_derived_claim"
        return EvidenceAssessment(
            True,
            reason,
            tuple(dict.fromkeys(row.candidate.candidate_id for row, _, _ in matches)),
            requirement.predicate,
            answer_state,
        )

    if request.intent in {
        RetrievalIntent.RELATIONSHIP,
        RetrievalIntent.EVENT,
        RetrievalIntent.RELATIONSHIP_OR_EVENT,
    }:
        return EvidenceAssessment(False, "unclassified_proposition")

    factual_rows = tuple(bundle.facts)
    for row in factual_rows:
        candidate = row.candidate
        quality = _evidence_quality(candidate, index)
        if quality not in {"direct", "unknown"}:
            continue
        if not _entities_compatible(candidate, request):
            continue
        return EvidenceAssessment(
            True,
            "trusted_direct_fact" if quality == "direct" else "trusted_explicit_unknown",
            (candidate.candidate_id,),
            answer_state="supported" if quality == "direct" else "unknown",
        )
    return EvidenceAssessment(False, "trusted_answerable_fact_missing")

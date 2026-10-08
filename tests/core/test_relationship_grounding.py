import json
from dataclasses import replace
from importlib.resources import files

import pytest

from hina_bot.ai.retrieval_request import build_resolved_retrieval_request
from hina_bot.ai.routing_plan import RoutingPlan
from hina_bot.core.admin_db import AdminDatabase
from hina_bot.core.knowledge_retrieval import KnowledgeCandidate, KnowledgeUsage
from hina_bot.core.lore import LoreIndex
from hina_bot.core.relationship_grounding import RelationshipGrounder
from hina_bot.core.retrieval_v2 import (
    KnowledgeBundle,
    RetrievalRequest,
    UsageBudget,
    lexical_bundle,
)
from hina_bot.core.runtime_knowledge import RuntimeKnowledgeRegistry

HINA = "character.hina"
HOSHINO = "character.hoshino"
PAIR = (HINA, HOSHINO)
BUDGET = UsageBudget(6, 3200)


def request(entities=PAIR):
    return RetrievalRequest("", "", entities=entities, required_entities=entities)


def row(identifier="pair", **overrides):
    return replace(KnowledgeCandidate(
        candidate_id=identifier, reference=identifier, source="static_lore", kind="world_fact",
        content="reviewed grounding", search_text="reviewed grounding", subjects=(), keywords=(),
        entities=PAIR, usages=(KnowledgeUsage.RELATION,), lane="canon", fact_type="fact_direct",
        confidence="crosschecked", kr_release="confirmed", awareness="direct_experience",
        time="reviewed timeline", source_metadata=(("type", "curated"),),
    ), **overrides)


def test_hoshino_regression_has_profiles_positive_year_comparison_and_direct_relation():
    bundle = RelationshipGrounder.load().bundle(
        build_resolved_retrieval_request(RoutingPlan("호시노는 선배야?", "호시노는 선배야?")),
        budget=BUDGET,
    )
    selected = {item.candidate.candidate_id: item.candidate for item in bundle.relations}
    assert not bundle.facts
    assert len(selected) == 5
    hina = selected["canon.hina.basic.profile"]
    hoshino = selected["canon.hoshino.basic.profile"]
    comparison = selected["canon.hina_hoshino.same_school_year"]
    assert hina.entities == (HINA,) and hoshino.entities == (HOSHINO,)
    assert "3학년" in hina.content and "게헨나" in hina.content
    assert "3학년" in hoshino.content and "아비도스" in hoshino.content
    assert set(comparison.evidence_ids) == {hina.candidate_id, hoshino.candidate_id}
    assert comparison.fact_type == comparison.awareness == "inference"
    assert comparison.reference_item()["kind"] == "interpretation"
    assert selected["canon.hina.first_meeting_with_hoshino_vol1"].awareness == "direct_experience"
    addressing = selected["canon.relationship_closer_after_fight_with_set_and_hoshino"]
    original = next(c for c in LoreIndex.load().candidates() if c.candidate_id == addressing.candidate_id)
    assert addressing == original  # Preserve directional wording, source, and timeline exactly.
    assert all(item.score == 1 for item in bundle.relations)


@pytest.mark.parametrize("query", ["", "unrelated lexical words", "age respect strength", "호시노"])
def test_exact_grounding_never_depends_on_generic_factual_slot_or_query(query):
    req = replace(request(), retrieval_text=query)
    relation = row()
    factual = row("factual", usages=(KnowledgeUsage.FACTUAL,), keywords=(query or "x",))
    base = lexical_bundle(req, [factual], budgets={KnowledgeUsage.FACTUAL: UsageBudget(0, 0)})
    grounded = RelationshipGrounder([relation]).bundle(req, budget=BUDGET, base=base)
    assert len(grounded.relations) == 1 and not grounded.facts
    base = lexical_bundle(req, [factual], budgets={KnowledgeUsage.FACTUAL: BUDGET})
    assert RelationshipGrounder([relation]).bundle(req, budget=BUDGET, base=base).facts == base.facts


def test_pair_must_match_exactly_and_profile_is_only_background():
    rows = [
        row("wrong_pair", entities=(HINA, "character.other")),
        row("superset", entities=(*PAIR, "character.other")),
        row("unrelated_profile", entities=("character.other",)),
        row("profile", entities=(HINA,)), row("correct"),
    ]
    result = RelationshipGrounder(rows).ground(request(), budget=BUDGET)
    assert [item.candidate.candidate_id for item in result] == ["correct", "profile"]
    assert [item.candidate.candidate_id for item in RelationshipGrounder(rows).ground(
        request((HINA,)), budget=BUDGET,
    )] == ["profile"]


def test_pair_evidence_is_reserved_before_profiles_under_small_budget():
    rows = [
        row("hina_profile", entities=(HINA,)),
        row("hoshino_profile", entities=(HOSHINO,)),
        row("pair_evidence"),
    ]
    selected = RelationshipGrounder(rows).ground(request(), budget=UsageBudget(1, 3200))
    assert [item.candidate.candidate_id for item in selected] == ["pair_evidence"]


def test_age_rank_respect_and_cross_school_years_never_generate_relationship_or_addressing():
    for content in (
        "Hina: age 17; Hoshino: age 18", "Hina: year 2 school A; Hoshino: year 3 school B",
        "Hina respects Hoshino's strength and title",
    ):
        profile = row("profile", entities=(HOSHINO,), content=content)
        selected = RelationshipGrounder([profile]).ground(request(), budget=BUDGET)
        assert [item.candidate for item in selected] == [profile]
        assert selected[0].candidate.reference_item() == profile.reference_item()
        # Even apparent profile content is not auto-promoted to relation usage.
        factual_only = replace(profile, usages=(KnowledgeUsage.FACTUAL,))
        assert not RelationshipGrounder([factual_only]).ground(request(), budget=BUDGET)


@pytest.mark.parametrize("override", [
    {"entities": ()}, {"usages": (KnowledgeUsage.FACTUAL,)}, {"lane": "community_meme"},
    {"confidence": None}, {"confidence": "candidate"}, {"kr_release": "pending"},
    {"source_metadata": ()}, {"awareness": "audience_only"}, {"fact_type": "fandom"},
    {"fact_type": "adaptation"}, {"fact_type": None}, {"fact_type": "inference"},
])
def test_unreviewed_unrelated_or_ineligible_rows_are_not_grounding(override):
    assert not RelationshipGrounder([row(**override)]).ground(request(), budget=BUDGET)


def test_reviewed_unknown_guard_is_preserved_but_absence_does_not_generate_one():
    unknown = row(
        fact_type="unknown", kind="interpretation", awareness="unknown",
        metadata=(("guard", "do_not_assert_positive_fact"),),
    )
    bundle = RelationshipGrounder([unknown]).bundle(request(), budget=BUDGET)
    assert bundle.context_sections()["relations"] == [unknown.reference_item()]
    assert RelationshipGrounder([]).bundle(request(), budget=BUDGET) == KnowledgeBundle()


@pytest.mark.parametrize("req", [
    RetrievalRequest("Hoshino", "Hoshino"),
    replace(request(), entities=(HINA,)),
    replace(request(), required_entities=()),
])
def test_unresolved_or_inconsistent_constraints_leave_relation_empty(req):
    assert not RelationshipGrounder.load().ground(req, budget=BUDGET)


def test_dedup_budget_skip_and_exact_score_threshold():
    small = row("small")
    big = row("big", content="x" * 2000)
    size = len(json.dumps(small.reference_item(), ensure_ascii=False))
    grounder = RelationshipGrounder([big, small, small])
    selected = grounder.ground(request(), budget=UsageBudget(5, size))
    assert [item.candidate for item in selected] == [small]
    for budget in (UsageBudget(0, 9999), UsageBudget(2, 0), UsageBudget(2, 9999, 1)):
        assert not grounder.ground(request(), budget=budget)
    assert len(grounder.ground(request(), budget=UsageBudget(1, 9999))) == 1


def test_runtime_metadata_is_not_synthesized_from_subject_or_admin_ownership():
    database = AdminDatabase(":memory:")
    try:
        registry = RuntimeKnowledgeRegistry(database, kind="world_fact")
        registry.add("pair", "히나 호시노 관계", "관계", "히나,호시노", "self")
        assert not RelationshipGrounder(registry.candidates()).ground(request(), budget=BUDGET)
    finally:
        database.close()


def test_legacy_results_are_identical_without_additive_grounding_annotations():
    index = LoreIndex.load()
    unannotated = LoreIndex([
        {key: value for key, value in record.items() if key not in {"usage", "entities"}}
        for record in index.records
    ])
    for query in ("호시노", "히나 호시노 무슨 관계", "학년", "히나 이름", "아비도스"):
        assert index.search(query) == unannotated.search(query)
    ids = {candidate.candidate_id for candidate in index.candidates()}
    assert "canon.hoshino.basic.profile" not in ids
    assert "canon.hina_hoshino.same_school_year" not in ids


def test_supplement_uses_valid_lore_schema_and_resolvable_evidence_ids():
    supplement = LoreIndex.load(str(files("hina_bot").joinpath("data/relationship_grounding.jsonl")))
    all_rows = [*LoreIndex.load().candidates(), *supplement.candidates()]
    ids = {item.candidate_id for item in all_rows}
    assert len(ids) == len(all_rows)
    for candidate in supplement.candidates():
        assert candidate.retrieval_usages == (KnowledgeUsage.RELATION,)
        assert set(candidate.evidence_ids) <= ids

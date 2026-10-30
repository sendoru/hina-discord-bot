"""Keep memory v2 fixtures (#449, #450) well-formed until their runners land.

The selection cases become executable in P2 and the extraction cases get a live
runner in P1 (personal) and P4 (server). Until then these tests only guard the
data contract: enum values, cross references and the absence of real identifiers.
The enum sets below mirror #449 §4 and should move to the real schema in P1.
"""

import json
import re
from datetime import date, datetime
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SELECTION_PATH = ROOT / "tests" / "fixtures" / "memory_v2" / "selection_cases.json"
EXTRACTION_PATH = ROOT / "evals" / "memory_extraction_cases.jsonl"

KINDS = {"fact", "event", "preference", "relationship", "boundary", "task"}
DISCLOSURES = {"local", "implicit", "reference_gated", "global"}
STATUSES = {"active", "superseded", "retracted"}
TIME_SCOPES = {"ongoing", "past", "planned", "episodic", "recurring", "unknown"}
STABILITIES = {"permanent", "long", "medium", "short"}
USAGE_MODES = {"directive", "ambient", "contextual", "sensitive"}
DIRECTIVE_TYPES = {"boundary", "style"}
AUDIENCES = {"owner", "server"}
TRUSTS = {"admin", "corroborated", "observed", "single"}
RELATIONS = {"duplicate", "corrects", "conflicts"}
AXES = {
    "familiarity", "comfort", "casualness", "teasing_tolerance",
    "support_openness", "task_orientation",
}
RENDER_STATUSES = {
    "current", "unverified", "upcoming", "just_passed", "past", "past_event", "recent_event",
}
PLACEMENTS = {
    "directives", "quiet_boundaries", "notes", "members", "server_directives", "server_notes",
}
TIERS = {"T0", "T1", "T2", "S0", "S1", "S2"}
EGRESS_POLICIES = {"full", "bot_interactions_only"}
FOLLOWUP_MODES = {"off", "dm", "all"}
SERVER_MEMORY_READ_MODES = {"on", "off"}

SPACE = re.compile(r"dm|g\d+:c\d+:(?:public|private)")
USER = re.compile(r"u\d+")
CHANNEL = re.compile(r"c\d+")
SERVER_SPACE = re.compile(r"guild|channel:c\d+")
RECURRENCE = re.compile(r"weekly:(?:MON|TUE|WED|THU|FRI|SAT|SUN) \d{2}:\d{2}")
EVENT_AT = re.compile(r"\d{4}-\d{2}(?:-\d{2})?")
# Discord snowflakes are 17+ digits; fixtures must only use symbolic ids.
SNOWFLAKE = re.compile(r"\d{15,}")


def _datetime(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    assert parsed.tzinfo is not None, value
    return parsed


def _event_at(value: str) -> None:
    assert EVENT_AT.fullmatch(value), value
    date.fromisoformat(value if len(value) == 10 else value + "-01")


def _selection():
    return json.loads(SELECTION_PATH.read_text(encoding="utf-8"))


def _extraction():
    return [
        json.loads(line)
        for line in EXTRACTION_PATH.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _check_memory(memory: dict, defaults: dict, refs: set[str]) -> None:
    assert set(memory) <= set(defaults) | {"ref", "content", "annotation"}, memory["ref"]
    merged = {**defaults, **memory}
    annotation = {**defaults["annotation"], **memory.get("annotation", {})}
    assert set(memory.get("annotation", {})) <= set(defaults["annotation"]), memory["ref"]
    assert merged["content"].strip()
    assert USER.fullmatch(merged["owner"])
    assert SPACE.fullmatch(merged["origin"])
    assert merged["kind"] in KINDS
    assert merged["disclosure"] in DISCLOSURES
    assert merged["status"] in STATUSES
    assert (merged["status"] == "superseded") == (merged["superseded_by"] is not None)
    if merged["superseded_by"] is not None:
        assert merged["superseded_by"] in refs
    assert set(merged["relationship_evidence"]) <= AXES
    if merged["relationship_evidence"]:
        assert merged["kind"] == "relationship"
    if not merged["annotated"]:
        assert "annotation" not in memory, memory["ref"]
        return

    assert annotation["time_scope"] in TIME_SCOPES
    if annotation["time_scope"] == "ongoing":
        assert annotation["stability"] in STABILITIES, memory["ref"]
    else:
        assert annotation["stability"] is None, memory["ref"]
    if annotation["time_scope"] == "recurring":
        assert RECURRENCE.fullmatch(annotation["recurrence"] or ""), memory["ref"]
    if annotation["time_scope"] == "planned":
        _event_at(annotation["event_at"])
    assert annotation["usage_mode"] in USAGE_MODES
    is_directive = annotation["usage_mode"] == "directive"
    assert (annotation["directive_type"] in DIRECTIVE_TYPES) == is_directive, memory["ref"]
    assert annotation["audience"] in AUDIENCES
    if annotation["usage_mode"] == "sensitive" or not merged["origin"].endswith(":public"):
        assert annotation["audience"] == "owner", memory["ref"]
    assert annotation["importance"] in {1, 2, 3}
    assert all(isinstance(value, str) and value for value in annotation["keywords"])
    assert annotation["cluster"] is None or annotation["cluster"] in refs
    assert 0 <= annotation["proactive_count"] <= 2
    assert annotation["item_revision"] <= merged["revision"]
    for key in ("observed_at", "last_confirmed_at", "last_used_at"):
        if annotation[key] is not None:
            _datetime(annotation[key])


def _check_server_memory(memory: dict, defaults: dict) -> None:
    assert set(memory) <= set(defaults) | {"ref", "content"}, memory["ref"]
    merged = {**defaults, **memory}
    assert merged["content"].strip()
    assert SERVER_SPACE.fullmatch(merged["space"])
    assert merged["trust"] in TRUSTS
    assert merged["time_scope"] in TIME_SCOPES
    assert merged["status"] in STATUSES
    assert all(USER.fullmatch(value) for value in merged["subject_users"] + merged["contributors"])
    # Write-time invariants from #449 §8.4 must also hold in fixtures.
    assert merged["usage_mode"] in USAGE_MODES - {"sensitive"}
    if merged["usage_mode"] == "directive":
        assert merged["trust"] == "admin", memory["ref"]
    if merged["usage_mode"] == "ambient":
        assert merged["trust"] in {"admin", "corroborated", "observed"}, memory["ref"]
    if merged["trust"] == "corroborated":
        assert len(set(merged["contributors"])) >= 2, memory["ref"]
    assert set(merged["subject_users"]) <= set(merged["contributors"]), memory["ref"]
    if merged["time_scope"] == "recurring":
        assert RECURRENCE.fullmatch(merged["recurrence"] or ""), memory["ref"]
    if merged["last_used_at"] is not None:
        _datetime(merged["last_used_at"])


def _check_expect(expect: dict, refs: set[str]) -> None:
    allowed = {
        "include", "exclude", "exactly_one_of", "placement", "tier", "render_status",
        "recall", "relationship_profile_axes", "server_note",
    }
    assert expect and set(expect) <= allowed
    assert expect.get("server_note", "included") in {"included", "excluded"}
    include = set(expect.get("include", ()))
    exclude = set(expect.get("exclude", ()))
    assert include <= refs and exclude <= refs
    assert not include & exclude
    for group in expect.get("exactly_one_of", ()):
        assert len(group) >= 2 and set(group) <= refs
    assert set(expect.get("placement", {})) <= include
    assert set(expect.get("placement", {}).values()) <= PLACEMENTS
    assert set(expect.get("tier", {})) <= include
    assert set(expect.get("tier", {}).values()) <= TIERS
    assert set(expect.get("render_status", {}).values()) <= RENDER_STATUSES
    assert set(expect.get("relationship_profile_axes", ())) <= AXES
    recall = expect.get("recall")
    if recall is not None:
        assert set(recall) <= {"query", "scope", "include", "exclude", "render_status"}
        assert recall["query"].strip() and recall["scope"] in {"personal", "server"}
        recall_include = set(recall.get("include", ()))
        assert recall_include <= refs and set(recall.get("exclude", ())) <= refs
        assert set(recall.get("render_status", {})) <= recall_include
        assert set(recall.get("render_status", {}).values()) <= RENDER_STATUSES


def test_selection_fixture_contract():
    fixture = _selection()
    defaults = fixture["defaults"]
    _datetime(defaults["now"])
    ids = [case["id"] for case in fixture["cases"]]
    assert len(ids) == len(set(ids))
    for case in fixture["cases"]:
        assert set(case) <= {
            "id", "covers", "note", "message", "scope", "speaker", "context", "settings",
            "server_note", "memories", "server_memories", "expect",
        }, case["id"]
        assert case["covers"].strip() and case["message"].strip()
        if "server_note" in case["expect"]:
            assert case.get("server_note", "").strip(), case["id"]
        assert SPACE.fullmatch(case.get("scope", defaults["scope"]))
        assert USER.fullmatch(case.get("speaker", defaults["speaker"]))
        context = {**defaults["context"], **case.get("context", {})}
        assert set(context) == set(defaults["context"]), case["id"]
        settings = {**defaults["settings"], **case.get("settings", {})}
        assert set(settings) == set(defaults["settings"]), case["id"]
        assert settings["MEMORY_SESSION_FOLLOWUP"] in FOLLOWUP_MODES
        assert settings["external_context_policy"] in EGRESS_POLICIES
        assert settings["SERVER_MEMORY_READ"] in SERVER_MEMORY_READ_MODES

        memories = case.get("memories", [])
        server_memories = case.get("server_memories", [])
        refs = [memory["ref"] for memory in memories + server_memories]
        assert refs and len(refs) == len(set(refs)), case["id"]
        for memory in memories:
            _check_memory(memory, defaults["memory"], set(refs))
        for memory in server_memories:
            _check_server_memory(memory, defaults["server_memory"])
        users = context["mentions"] + context["thread_participants"]
        users += context["member_opt_out"] + context["departed_members"]
        assert all(USER.fullmatch(user) for user in users)
        assert all(CHANNEL.fullmatch(value) for value in context["reader_denied_channels"])
        assert set(context["authorized_reference_gated"]) <= set(refs)
        _check_expect(case["expect"], set(refs))


CONSTRAINT_KEYS = {
    "match_all", "match_any", "not_match_any", "time_scope", "time_scope_in", "stability_in",
    "event_at", "recurrence_prefix", "usage_mode", "usage_mode_in", "directive_type",
    "audience", "importance_min", "explicit", "slot", "relation", "relation_absent",
    "trust", "subject_users", "subject_users_include",
}


def _check_constraint(constraint: dict, refs: set[str]) -> None:
    assert constraint and set(constraint) <= CONSTRAINT_KEYS, constraint
    for key in ("match_all", "match_any", "not_match_any"):
        assert all(isinstance(value, str) and value for value in constraint.get(key, ["x"]))
    assert constraint.get("time_scope", "ongoing") in TIME_SCOPES
    assert set(constraint.get("time_scope_in", ())) <= TIME_SCOPES
    assert set(constraint.get("stability_in", ())) <= STABILITIES
    assert constraint.get("usage_mode", "ambient") in USAGE_MODES
    assert set(constraint.get("usage_mode_in", ())) <= USAGE_MODES
    assert constraint.get("directive_type", "boundary") in DIRECTIVE_TYPES
    assert constraint.get("audience", "owner") in AUDIENCES
    assert constraint.get("trust", "single") in TRUSTS
    assert constraint.get("importance_min", 1) in {1, 2, 3}
    if "event_at" in constraint:
        _event_at(constraint["event_at"])
    if "relation" in constraint:
        assert constraint["relation"]["type"] in RELATIONS
        assert constraint["relation"]["target"] in refs
    users = constraint.get("subject_users", []) + [constraint.get("subject_users_include", "u1")]
    assert all(USER.fullmatch(user) for user in users)


def test_extraction_fixture_contract():
    cases = _extraction()
    ids = [case["id"] for case in cases]
    assert len(ids) == len(set(ids))
    for case in cases:
        assert set(case) <= {
            "id", "target", "space", "now", "turns", "existing_memories", "expected",
            "expect_items", "forbid_items", "expect_no_items",
        }, case["id"]
        assert case["target"] in {"personal", "server"}
        assert case["space"] in {"dm", "public", "private"}
        if case["target"] == "server":
            assert case["space"] != "dm", case["id"]
        assert case["expected"].strip()
        now = _datetime(case["now"])
        assert case["turns"]
        for turn in case["turns"]:
            assert _datetime(turn["at"]) <= now
            assert turn["user"].strip()
            if case["target"] == "server":
                assert USER.fullmatch(turn["user_id"]) and turn["speaker"].strip()
            else:
                assert "user_id" not in turn
        refs = {memory["ref"] for memory in case.get("existing_memories", [])}
        for memory in case.get("existing_memories", []):
            assert memory["content"].strip()
            assert set(memory.get("annotation", {})) <= {"time_scope", "stability", "slot"}
        expect_items = case.get("expect_items", [])
        forbid_items = case.get("forbid_items", [])
        assert expect_items or forbid_items or case.get("expect_no_items"), case["id"]
        if case.get("expect_no_items"):
            assert not expect_items, case["id"]
        for constraint in expect_items + forbid_items:
            _check_constraint(constraint, refs)
            if case["target"] == "personal":
                assert not {"trust", "subject_users", "subject_users_include"} & set(constraint)


@pytest.mark.parametrize("path", [SELECTION_PATH, EXTRACTION_PATH], ids=lambda path: path.name)
def test_memory_fixtures_use_symbolic_identifiers_only(path):
    assert not SNOWFLAKE.search(path.read_text(encoding="utf-8"))

"""The memory report aggregates operational memory without leaking content or ids."""

import hashlib
import json
from datetime import UTC, datetime

import pytest

from hina_bot.core.memory_items import MemoryDisclosure, MemoryKind
from hina_bot.core.routing import Scope
from hina_bot.core.store import Store
from hina_bot.tooling import memory_report

NOW = datetime(2026, 10, 30, tzinfo=UTC)
USER_A = 111111111111111111
USER_B = 555555555555555555
GUILD = 222222222222222222
PUBLIC_CHANNEL = 333333333333333333
PRIVATE_CHANNEL = 444444444444444444
DM_CHANNEL = 900000000000000001
SECRET_NAME = "비밀이름사용자"
SERVER_NOTE = "서버 비밀 메모 내용"
USER_NOTE = "사용자 비밀 메모 내용"
SUMMARY_TEXT = "공개 요약 비밀 문장"

DM = Scope(None, DM_CHANNEL, USER_A)
PUBLIC = Scope(GUILD, PUBLIC_CHANNEL, USER_A, public_at_capture=True)
PRIVATE = Scope(GUILD, PRIVATE_CHANNEL, USER_A)
PUBLIC_B = Scope(GUILD, PUBLIC_CHANNEL, USER_B, public_at_capture=True)

CONTENTS = {
    "cat_dm": "고양이 보리를 키움",
    "cat_public": "고양이 보리를 키움.",
    "hike_dm": "주말마다 동료들과 등산을 함",
    "hike_public": "주말마다 동료들과 등산을 감",
    "piano": "예전에 피아노를 쳤었음",
    "interview": "다음 주에 면접이 있음",
    "spicy": "매운 음식을 싫어함",
    "global_b": "모든 공간에서 존댓말을 원함",
    "relationship": "가벼운 장난을 편하게 주고받음",
}


def _add(store, scope, content, *, kind=MemoryKind.FACT, disclosure=MemoryDisclosure.LOCAL,
         confidence=0.9, evidence=None, source="1"):
    return store.add_memory_item(
        scope,
        content,
        kind=kind,
        disclosure=disclosure,
        source_message_ids=(source,),
        confidence=confidence,
        relationship_evidence=evidence,
        user_name=SECRET_NAME,
    )


def _proposal(store, scope, new_id, target_id, relation, confidence):
    proposal_id = store.add_memory_reconciliation_proposal(
        scope,
        new_memory_item_id=new_id,
        target_memory_item_id=target_id,
        relation=relation,
        confidence=confidence,
        source_message_ids=("9",),
    )
    assert proposal_id is not None
    return proposal_id


@pytest.fixture
def database(tmp_path):
    path = tmp_path / "hina.sqlite3"
    store = Store.create(str(path))
    try:
        _add(store, DM, CONTENTS["cat_dm"])
        _add(store, PUBLIC, CONTENTS["cat_public"])
        _add(store, DM, CONTENTS["hike_dm"])
        _add(store, PUBLIC, CONTENTS["hike_public"])
        _add(store, PUBLIC, CONTENTS["piano"])
        interview = _add(store, PUBLIC, CONTENTS["interview"])
        _add(store, PRIVATE, CONTENTS["spicy"], kind=MemoryKind.PREFERENCE)
        _add(store, PUBLIC_B, CONTENTS["global_b"], kind=MemoryKind.PREFERENCE,
             disclosure=MemoryDisclosure.GLOBAL)
        _add(store, DM, CONTENTS["relationship"], kind=MemoryKind.RELATIONSHIP,
             disclosure=MemoryDisclosure.IMPLICIT, evidence={"comfort": 2, "familiarity": 3})
        store.db.execute(
            "UPDATE memory_items SET created_at='2026-09-01 00:00:00' WHERE id=?",
            (interview,),
        )
        store.db.commit()

        # Applied duplicate: the new item is superseded by its target.
        target = _add(store, PUBLIC, "중복 대상 기억", source="2")
        duplicate = _add(store, PUBLIC, "중복 신규 기억", source="3")
        proposal = _proposal(store, PUBLIC, duplicate, target, "duplicate", 0.99)
        assert store.apply_memory_reconciliation_proposal(proposal) == "applied"

        # A pre-v2 high-confidence duplicate left unapplied by the old fact/preference
        # check; the report still classifies such historical rows.
        fact = _add(store, PUBLIC, "커피를 못 마심", source="4")
        preference = _add(store, PUBLIC, "커피를 마시지 못함", kind=MemoryKind.PREFERENCE,
                          source="5")
        _proposal(store, PUBLIC, preference, fact, "duplicate", 0.99)

        low_old = _add(store, PUBLIC, "낮은 신뢰 이전 기억", source="6")
        low_new = _add(store, PUBLIC, "낮은 신뢰 새 기억", source="7")
        _proposal(store, PUBLIC, low_new, low_old, "corrects", 0.5)

        conflict_old = _add(store, PUBLIC, "충돌 이전 기억", source="8")
        conflict_new = _add(store, PUBLIC, "충돌 새 기억", source="10")
        _proposal(store, PUBLIC, conflict_new, conflict_old, "conflicts", 0.8)

        edited = _add(store, DM, "편집 전 기억", source="11")
        store.revise_memory_item(
            edited,
            expected_revision=0,
            content="편집 후 기억",
            kind=MemoryKind.FACT,
            disclosure=MemoryDisclosure.LOCAL,
            confidence=0.9,
        )
        retracted = _add(store, DM, "철회된 기억", source="12")
        store.retract_memory_item(retracted, expected_revision=0)

        for message_id in (7001, 7002, 7003):
            store.record_completed_turn(
                PUBLIC, message_id, "사용자 비밀 발화", "히나 비밀 답변", name=SECRET_NAME,
            )
        store.record_completed_turn(DM, 7004, "DM 비밀 발화", "DM 비밀 답변", name=SECRET_NAME)
        store.save_memory_extraction_cursor(PUBLIC, 0)
        store.set_note(PUBLIC.realm, SERVER_NOTE)
        store.set_note(PUBLIC.user_note, USER_NOTE)
        store.set_memory_mode_override(PUBLIC.realm, "read_only")
        store.db.execute(
            "INSERT INTO shared_summaries VALUES (?,?,?,?,?,?)",
            (PUBLIC.conversation, PUBLIC.realm, str(USER_A), SECRET_NAME, SUMMARY_TEXT, 1),
        )
        store.db.commit()
    finally:
        store.close()
    return path


@pytest.fixture
def usage_log(tmp_path):
    path = tmp_path / "usage.jsonl"
    rotated = tmp_path / "usage.jsonl.1"
    # Item counts and character sizes arrive as separate rows joined by turn_id.
    rows = [
        {"at": "2026-10-28T00:00:00+00:00", "operation": "context.provenance",
         "turn_id": "t1", "context_structured_items": 3, "context_public_items": 1,
         "factual_recall_status": "no_explicit_reference"},
        {"at": "2026-10-28T00:00:00+00:00", "operation": "context.size", "turn_id": "t1",
         "context_structured_memory_chars": 300, "context_chars_total": 3000,
         "context_public_chars": 200},
        {"at": "2026-10-29T00:00:00+00:00", "operation": "context.provenance",
         "turn_id": "t2", "context_structured_items": 0, "context_public_items": 0,
         "factual_recall_status": "owner_dm"},
        {"at": "2026-10-29T00:00:00+00:00", "operation": "context.size", "turn_id": "t2",
         "context_structured_memory_chars": 100, "context_chars_total": 1000,
         "context_public_chars": 2},
        {"at": "2026-10-29T00:00:01+00:00", "operation": "memory.shadow_extraction",
         "status": "completed", "batch_turns": 4, "accepted_items": 2, "stored_items": 1,
         "duplicate_items": 1, "rejected_items": 0},
        {"at": "2026-10-29T00:00:02+00:00", "operation": "memory.shadow_extraction",
         "status": "invalid"},
    ]
    rotated.write_text(
        "".join(json.dumps(row) + "\n" for row in rows[:2]), encoding="utf-8",
    )
    path.write_text(
        "".join(json.dumps(row) + "\n" for row in rows[2:]) + "not json\n",
        encoding="utf-8",
    )
    return path


def test_report_aggregates_memory_quality_signals(database, usage_log):
    report = memory_report.build_report(database, usage_log_path=usage_log, now=NOW)
    memory = report["database"]["memory_items"]

    assert memory["status"] == {"active": 17, "superseded": 1, "retracted": 1}
    active = memory["active"]
    assert active["users"] == 2
    assert active["origin_type"]["dm"] == 4
    assert active["origin_type"]["guild_private"] == 1
    assert active["kind"]["relationship"] == 1
    assert active["edited_items"] == 1
    assert memory["edit_history_rows"] == 1

    duplicates = memory["duplicates_non_relationship"]
    assert duplicates["exact"] == {
        "groups": 1, "items": 2, "cross_space_groups": 1, "cross_space_items": 2,
    }
    assert duplicates["near"]["pairs_cross_space"] == 1
    assert duplicates["near"]["users_with_cross_space"] == 1

    temporal = memory["temporal_signals"]
    assert temporal["past_marker_items"] == 1
    assert temporal["relative_date_items"] == 1
    assert temporal["relative_date_items_older_than_14d"] == 1

    assert memory["relationship"]["with_evidence"] == 1
    assert memory["relationship"]["axis_levels"]["familiarity"] == {"3": 1}

    injection = memory["current_injection_estimate"]
    assert injection["dm_all_active"]["items_per_user"]["max"] == 16
    assert injection["guild_public_full_non_relationship"]["items"]["count"] == 2

    proposals = report["database"]["reconciliation_proposals"]
    assert proposals["total"] == 4
    assert proposals["by_relation"]["duplicate"] == {"applied": 1, "kind_mismatch": 1}
    assert proposals["by_relation"]["corrects"] == {"below_threshold": 1}
    assert proposals["kind_mismatch_between_non_relationship"] == 1
    assert proposals["unresolved_conflicts"] == 1
    assert proposals["confidence_buckets"]["duplicate:general"][">=0.95"] == 2
    assert proposals["confidence_buckets"]["corrects:general"]["<0.5"] == 0
    assert proposals["confidence_buckets"]["corrects:general"]["0.5-0.7"] == 1
    assert proposals["confidence_buckets"]["conflicts:general"]["0.8-0.85"] == 1

    by_status = memory["by_status"]
    assert by_status["superseded"]["total"] == 1
    assert by_status["retracted"]["kind"] == {"fact": 1}
    assert by_status["active"]["temporal_signals"]["past_marker_items"] == 1
    # Full history still sees the cross-space duplicate plus the superseded pair.
    assert memory["duplicates_all_statuses_non_relationship"]["exact"]["groups"] == 1
    assert memory["supersede_provenance"] == {
        "superseded": 1, "with_matching_proposal": 1,
        "without_matching_proposal": 0, "replacement_missing": 0,
    }

    assert report["database"]["turns"]["total"] == 4
    assert report["database"]["extraction_backlog"]["scopes_with_pending"] == 2
    assert report["database"]["shared"]["shared_summaries"]["non_empty"] == 1
    assert report["database"]["notes"]["server"]["count"] == 1
    assert report["database"]["notes"]["user"]["count"] == 1
    assert report["database"]["memory_modes"] == {"realm": {"read_only": 1}}


def test_report_aggregates_rotated_usage_telemetry(database, usage_log):
    telemetry = memory_report.build_report(
        database, usage_log_path=usage_log, now=NOW,
    )["telemetry"]

    assert telemetry["usage_files"] == 2
    provenance = telemetry["context_provenance"]
    assert provenance["turns"] == 2
    assert provenance["turns_with_size_row"] == 2
    assert provenance["public_server_context_turns"] == 1
    assert provenance["public_server_context_rate"] == 0.5
    assert provenance["structured_share_of_context_chars"]["max"] == 0.1
    assert provenance["structured_memory_chars_when_present"] == {
        "count": 1, "min": 300, "p50": 300, "p90": 300, "max": 300, "mean": 300,
    }
    assert provenance["public_context_chars_when_present"]["max"] == 200
    assert provenance["context_chars_total"]["max"] == 3000
    assert provenance["factual_recall_status"] == {"no_explicit_reference": 1, "owner_dm": 1}
    extraction = telemetry["shadow_extraction"]
    assert extraction["status"] == {"completed": 1, "invalid": 1}
    assert extraction["completed_totals"]["accepted_items"] == 2
    assert extraction["completed_totals"]["stored_items"] == 1


def test_report_never_contains_content_names_or_discord_ids(database, usage_log):
    output = json.dumps(
        memory_report.build_report(database, usage_log_path=usage_log, now=NOW),
        ensure_ascii=False,
    )

    sensitive = [
        *CONTENTS.values(), SECRET_NAME, SERVER_NOTE, USER_NOTE, SUMMARY_TEXT,
        "사용자 비밀 발화", "히나 비밀 답변", "편집 후 기억", "커피",
        *(str(value) for value in (
            USER_A, USER_B, GUILD, PUBLIC_CHANNEL, PRIVATE_CHANNEL, DM_CHANNEL, 7001,
        )),
    ]
    for value in sensitive:
        assert value not in output


def test_report_opens_database_read_only(database):
    before = hashlib.sha256(database.read_bytes()).hexdigest()
    memory_report.build_report(database, now=NOW)
    assert hashlib.sha256(database.read_bytes()).hexdigest() == before


def test_cli_prints_json_and_can_skip_telemetry(database, capsys):
    assert memory_report.main(["--db", str(database), "--usage-log", ""]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["report"] == "hina-memory-report"
    assert report["telemetry"] is None
    assert report["database"]["memory_items"]["active"]["total"] == 17


def test_cli_rejects_invalid_near_threshold(database):
    with pytest.raises(SystemExit):
        memory_report.main(["--db", str(database), "--near-threshold", "0"])

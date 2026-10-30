"""Content-free aggregate report of structured memory for memory v2 planning (#450).

The runtime SQLite database and usage telemetry are opened read-only. Output holds
counts, ratios and distribution summaries only: never memory text, utterances,
names, notes, or Discord user/guild/channel/message identifiers. Run it locally
against the operational files and share only the printed JSON.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import unicodedata
from collections import Counter, defaultdict
from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path

from hina_bot.core.memory_items import RELATIONSHIP_EVIDENCE_AXES
from hina_bot.dashboard.telemetry import TelemetryReader

from .maintenance_db import connect_existing, table_exists

REPORT_VERSION = 1
# Mirrors the extractor's current automatic-application threshold.
AUTO_APPLY_THRESHOLD = 0.95
DEFAULT_NEAR_DUPLICATE_THRESHOLD = 0.8

_AGE_BUCKETS = ((7, "<7d"), (30, "7-30d"), (90, "30-90d"), (180, "90-180d"), (365, "180-365d"))
_CONFIDENCE_BUCKETS = (
    (0.5, "<0.5"), (0.7, "0.5-0.7"), (0.85, "0.7-0.85"), (0.95, "0.85-0.95"),
)
_USER_ITEM_BUCKETS = ((10, "1-9"), (25, "10-24"), (50, "25-49"), (100, "50-99"))

# Heuristic signals only; counts never reveal which items matched.
_PAST_MARKER = re.compile(
    r"었었|았었|였었|던\s*적|시절|예전에|옛날에|어릴\s*때|재작년|작년|그만뒀|그만두었"
)
_RELATIVE_DATE = re.compile(
    r"오늘|내일|모레|어제|그제|이번\s*주|다음\s*주|지난\s*주|이번\s*달|다음\s*달|지난\s*달|"
    r"이번\s*주말|다음\s*주말"
)
_ABSOLUTE_DATE = re.compile(r"\d{4}-\d{2}-\d{2}|\d{1,2}\s*월\s*\d{1,2}\s*일")
_STALE_RELATIVE_DAYS = 14


def _parse_time(value) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace(" ", "T", 1))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _age_days(value, now: datetime) -> float | None:
    parsed = _parse_time(value)
    if parsed is None:
        return None
    return max(0.0, (now - parsed).total_seconds() / 86400)


def _quantiles(values: Iterable[float]) -> dict[str, float | int]:
    ordered = sorted(values)
    if not ordered:
        return {"count": 0}

    def rank(fraction: float):
        # Nearest-rank keeps reported values equal to observed values.
        index = max(0, min(len(ordered) - 1, int(fraction * len(ordered) + 0.999999) - 1))
        return _round(ordered[index])

    return {
        "count": len(ordered),
        "min": _round(ordered[0]),
        "p50": rank(0.5),
        "p90": rank(0.9),
        "max": _round(ordered[-1]),
        "mean": _round(sum(ordered) / len(ordered)),
    }


def _round(value: float) -> float | int:
    return int(value) if float(value).is_integer() else round(float(value), 3)


def _bucket(value: float, edges: tuple[tuple[float, str], ...], last: str) -> str:
    for edge, label in edges:
        if value < edge:
            return label
    return last


def _bucket_counts(values: Iterable[float], edges, last: str) -> dict[str, int]:
    labels = [label for _edge, label in edges] + [last]
    counts = Counter(_bucket(value, edges, last) for value in values)
    return {label: counts.get(label, 0) for label in labels}


def _ratio(part: int, whole: int) -> float:
    return round(part / whole, 4) if whole else 0.0


def _origin_type(row) -> str:
    if str(row["origin_realm"]).startswith("dm:"):
        return "dm"
    return "guild_public" if int(row["origin_public_at_capture"]) else "guild_private"


def _origin_space(row) -> tuple[str, ...]:
    """Disclosure-space key used only for in-process grouping, never emitted."""

    origin = _origin_type(row)
    if origin == "guild_private":
        return (origin, str(row["origin_realm"]), str(row["origin_channel_id"]))
    return (origin, str(row["origin_realm"]))


def _normalized(text: str) -> str:
    return re.sub(r"[\W_]+", "", unicodedata.normalize("NFKC", text).casefold())


def _bigrams(text: str) -> frozenset[str]:
    return frozenset(text[index:index + 2] for index in range(len(text) - 1))


def _jaccard(left: frozenset[str], right: frozenset[str]) -> float:
    if not left or not right:
        return 0.0
    return len(left & right) / len(left | right)


def _json_object(value) -> dict:
    try:
        parsed = json.loads(value or "{}")
    except (TypeError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _duplicates(rows, threshold: float) -> dict:
    """Count exact and near-duplicate active factual memory within each owner."""

    by_user: dict[str, list] = defaultdict(list)
    for row in rows:
        by_user[str(row["user_id"])].append(row)

    exact = {"groups": 0, "items": 0, "cross_space_groups": 0, "cross_space_items": 0}
    near = {"pairs_same_space": 0, "pairs_cross_space": 0, "users_with_cross_space": 0}
    for items in by_user.values():
        groups: dict[str, list] = defaultdict(list)
        for row in items:
            groups[_normalized(str(row["content"]))].append(row)
        for members in groups.values():
            if len(members) < 2:
                continue
            exact["groups"] += 1
            exact["items"] += len(members)
            if len({_origin_space(row) for row in members}) > 1:
                exact["cross_space_groups"] += 1
                exact["cross_space_items"] += len(members)

        prepared = [
            (_normalized(str(row["content"])), _origin_space(row)) for row in items
        ]
        grams = [_bigrams(text) for text, _space in prepared]
        user_cross = False
        for left in range(len(prepared)):
            for right in range(left + 1, len(prepared)):
                if prepared[left][0] == prepared[right][0]:
                    continue
                if _jaccard(grams[left], grams[right]) < threshold:
                    continue
                if prepared[left][1] == prepared[right][1]:
                    near["pairs_same_space"] += 1
                else:
                    near["pairs_cross_space"] += 1
                    user_cross = True
        near["users_with_cross_space"] += int(user_cross)
    return {"exact": exact, "near": {"threshold": threshold, **near}}


def _injection_estimate(rows) -> dict:
    """Approximate today's always-on injection sizes from active item ownership."""

    dm_items: dict[str, list] = defaultdict(list)
    guild_items: dict[tuple[str, str], list] = defaultdict(list)
    globals_by_user: dict[str, list] = defaultdict(list)
    for row in rows:
        user = str(row["user_id"])
        dm_items[user].append(row)
        if str(row["kind"]) == "relationship":
            continue
        realm = str(row["origin_realm"])
        if realm.startswith("guild:") and int(row["origin_public_at_capture"]):
            guild_items[(user, realm)].append(row)
        if str(row["disclosure"]) == "global":
            globals_by_user[user].append(row)

    guild_counts = []
    guild_chars = []
    for (user, realm), local in guild_items.items():
        visible = {int(row["id"]): row for row in local}
        for row in globals_by_user.get(user, ()):
            if str(row["origin_realm"]) != realm:
                visible[int(row["id"])] = row
        guild_counts.append(len(visible))
        guild_chars.append(sum(len(str(row["content"])) for row in visible.values()))

    return {
        "dm_all_active": {
            "items_per_user": _quantiles(len(items) for items in dm_items.values()),
            "content_chars_per_user": _quantiles(
                sum(len(str(row["content"])) for row in items) for items in dm_items.values()
            ),
        },
        "guild_public_full_non_relationship": {
            "note": "per (user, guild) with public-channel items; plus global items",
            "items": _quantiles(guild_counts),
            "content_chars": _quantiles(guild_chars),
        },
    }


def _proposal_outcomes(db: sqlite3.Connection) -> dict:
    if not table_exists(db, "memory_reconciliation_proposals"):
        return {}
    rows = db.execute(
        """SELECT p.relation,p.confidence,p.new_memory_item_id,p.target_memory_item_id,
                  n.kind AS new_kind,n.status AS new_status,n.superseded_by AS new_by,
                  t.kind AS target_kind,t.status AS target_status,t.superseded_by AS target_by
           FROM memory_reconciliation_proposals p
           LEFT JOIN memory_items n ON n.id=p.new_memory_item_id
           LEFT JOIN memory_items t ON t.id=p.target_memory_item_id"""
    ).fetchall()
    by_relation: dict[str, Counter] = defaultdict(Counter)
    blocked_kind_high_confidence = 0
    unresolved_conflicts = 0
    for row in rows:
        relation = str(row["relation"])
        outcome = by_relation[relation]
        if row["new_kind"] is None or row["target_kind"] is None:
            outcome["missing_item"] += 1
            continue
        if relation == "conflicts":
            outcome["conflict_deferred"] += 1
            if row["new_status"] == "active" and row["target_status"] == "active":
                unresolved_conflicts += 1
            continue
        new_id = int(row["new_memory_item_id"])
        target_id = int(row["target_memory_item_id"])
        applied = (
            relation == "duplicate"
            and row["new_status"] == "superseded"
            and row["new_by"] is not None
            and int(row["new_by"]) == target_id
        ) or (
            relation == "corrects"
            and row["target_status"] == "superseded"
            and row["target_by"] is not None
            and int(row["target_by"]) == new_id
        )
        if applied:
            outcome["applied"] += 1
        elif float(row["confidence"]) < AUTO_APPLY_THRESHOLD:
            outcome["below_threshold"] += 1
        elif row["new_kind"] != row["target_kind"]:
            outcome["kind_mismatch"] += 1
            if "relationship" not in {row["new_kind"], row["target_kind"]}:
                blocked_kind_high_confidence += 1
        elif row["new_kind"] == "relationship":
            outcome["relationship_deferred"] += 1
        else:
            outcome["other_not_applied"] += 1
    return {
        "total": len(rows),
        "by_relation": {key: dict(value) for key, value in sorted(by_relation.items())},
        "kind_mismatch_between_non_relationship": blocked_kind_high_confidence,
        "unresolved_conflicts": unresolved_conflicts,
    }


def _temporal_signals(rows, now: datetime) -> dict:
    past = relative = stale_relative = absolute = 0
    for row in rows:
        content = str(row["content"])
        if _PAST_MARKER.search(content):
            past += 1
        if _ABSOLUTE_DATE.search(content):
            absolute += 1
        if _RELATIVE_DATE.search(content):
            relative += 1
            age = _age_days(row["created_at"], now)
            if age is not None and age >= _STALE_RELATIVE_DAYS:
                stale_relative += 1
    return {
        "note": "regex heuristics over active non-relationship content; counts only",
        "past_marker_items": past,
        "relative_date_items": relative,
        f"relative_date_items_older_than_{_STALE_RELATIVE_DAYS}d": stale_relative,
        "absolute_date_items": absolute,
    }


def _relationship_summary(rows) -> dict:
    axes: dict[str, Counter] = {axis: Counter() for axis in RELATIONSHIP_EVIDENCE_AXES}
    with_evidence = 0
    for row in rows:
        evidence = _json_object(row["relationship_evidence"])
        if evidence:
            with_evidence += 1
        for axis, level in evidence.items():
            if axis in axes and isinstance(level, int) and level > 0:
                axes[axis][str(level)] += 1
    return {
        "active_items": len(rows),
        "with_evidence": with_evidence,
        "axis_levels": {axis: dict(sorted(counts.items())) for axis, counts in axes.items()},
    }


def _memory_items_section(db: sqlite3.Connection, now: datetime, threshold: float) -> dict:
    rows = db.execute("SELECT * FROM memory_items").fetchall()
    active = [row for row in rows if row["status"] == "active"]
    factual = [row for row in active if row["kind"] != "relationship"]
    relationship = [row for row in active if row["kind"] == "relationship"]
    per_user = Counter(str(row["user_id"]) for row in active)
    edit_rows = (
        int(db.execute("SELECT COUNT(*) FROM memory_item_edit_history").fetchone()[0])
        if table_exists(db, "memory_item_edit_history")
        else 0
    )
    ages = [
        age for age in (_age_days(row["created_at"], now) for row in active) if age is not None
    ]
    return {
        "status": dict(Counter(str(row["status"]) for row in rows)),
        "active": {
            "total": len(active),
            "kind": dict(Counter(str(row["kind"]) for row in active)),
            "disclosure": dict(Counter(str(row["disclosure"]) for row in active)),
            "origin_type": dict(Counter(_origin_type(row) for row in active)),
            "kind_by_disclosure": {
                kind: dict(Counter(
                    str(row["disclosure"]) for row in active if row["kind"] == kind
                ))
                for kind in sorted({str(row["kind"]) for row in active})
            },
            "users": len(per_user),
            "items_per_user": _quantiles(per_user.values()),
            "items_per_user_buckets": _bucket_counts(
                per_user.values(), _USER_ITEM_BUCKETS, "100+"
            ),
            "age_days": _quantiles(ages),
            "age_buckets": _bucket_counts(ages, _AGE_BUCKETS, "365d+"),
            "content_chars": _quantiles(len(str(row["content"])) for row in active),
            "confidence_buckets": _bucket_counts(
                (float(row["confidence"]) for row in active), _CONFIDENCE_BUCKETS, ">=0.95"
            ),
            "edited_items": sum(1 for row in active if int(row["revision"]) > 0),
            "edited_ratio": _ratio(
                sum(1 for row in active if int(row["revision"]) > 0), len(active)
            ),
        },
        "edit_history_rows": edit_rows,
        "duplicates_non_relationship": _duplicates(factual, threshold),
        "current_injection_estimate": _injection_estimate(active),
        "temporal_signals": _temporal_signals(factual, now),
        "relationship": _relationship_summary(relationship),
    }


def _extraction_backlog(db: sqlite3.Connection, now: datetime) -> dict:
    if not table_exists(db, "memory_extraction_cursors"):
        return {}
    rows = db.execute(
        """SELECT COUNT(*) AS pending, MIN(t.created_at) AS oldest
           FROM turns t
           LEFT JOIN memory_extraction_cursors c ON c.scope=t.scope
           WHERE t.id > COALESCE(c.through_id,0)
           GROUP BY t.scope"""
    ).fetchall()
    ages = [
        age for age in (_age_days(row["oldest"], now) for row in rows) if age is not None
    ]
    return {
        "scopes_with_pending": len(rows),
        "pending_turns_per_scope": _quantiles(int(row["pending"]) for row in rows),
        "oldest_pending_age_days": _quantiles(ages),
    }


def _turns_section(db: sqlite3.Connection, now: datetime) -> dict:
    rows = db.execute(
        "SELECT COUNT(*) AS turns, MIN(created_at) AS oldest FROM turns GROUP BY scope"
    ).fetchall()
    ages = [
        age for age in (_age_days(row["oldest"], now) for row in rows) if age is not None
    ]
    return {
        "total": sum(int(row["turns"]) for row in rows),
        "scopes": len(rows),
        "turns_per_scope": _quantiles(int(row["turns"]) for row in rows),
        "oldest_turn_age_days_per_scope": _quantiles(ages),
    }


def _shared_section(db: sqlite3.Connection) -> dict:
    result: dict[str, object] = {}
    if table_exists(db, "shared_summaries"):
        lengths = [
            len(str(row[0] or ""))
            for row in db.execute("SELECT text FROM shared_summaries").fetchall()
        ]
        result["shared_summaries"] = {
            "rows": len(lengths),
            "non_empty": sum(1 for value in lengths if value),
            "text_chars": _quantiles(value for value in lengths if value),
        }
    if table_exists(db, "shared_calls"):
        result["shared_calls_rows"] = int(
            db.execute("SELECT COUNT(*) FROM shared_calls").fetchone()[0]
        )
    return result


def _notes_section(db: sqlite3.Connection) -> dict:
    if not table_exists(db, "notes"):
        return {}
    lengths: dict[str, list[int]] = {"server": [], "user": []}
    other_keys = 0
    for row in db.execute("SELECT scope,text FROM notes").fetchall():
        scope = str(row["scope"])
        if ":user:" in scope:
            key = "user"
        elif re.fullmatch(r"guild:\d+", scope):
            key = "server"
        else:
            # The notes table also carries internal markers such as config flags.
            other_keys += 1
            continue
        length = len(str(row["text"] or ""))
        if length:
            lengths[key].append(length)
    return {
        **{key: _quantiles(values) for key, values in lengths.items()},
        "other_keys": other_keys,
    }


def _memory_mode_section(db: sqlite3.Connection) -> dict:
    if not table_exists(db, "memory_modes"):
        return {}
    counts: dict[str, Counter] = defaultdict(Counter)
    for row in db.execute("SELECT scope,mode FROM memory_modes").fetchall():
        scope = str(row["scope"])
        if scope == "global":
            target = "global"
        elif scope.startswith("user:"):
            target = "user"
        elif ":channel:" in scope:
            target = "channel"
        elif scope.startswith(("guild:", "dm:")):
            target = "realm"
        else:
            target = "other"
        counts[target][str(row["mode"])] += 1
    return {key: dict(value) for key, value in sorted(counts.items())}


def database_report(
    db: sqlite3.Connection,
    *,
    now: datetime,
    near_threshold: float = DEFAULT_NEAR_DUPLICATE_THRESHOLD,
) -> dict:
    """Aggregate a borrowed read-only connection; never returns row content or ids."""

    report: dict[str, object] = {
        "turns": _turns_section(db, now),
        "extraction_backlog": _extraction_backlog(db, now),
        "reconciliation_proposals": _proposal_outcomes(db),
        "shared": _shared_section(db),
        "notes": _notes_section(db),
        "memory_modes": _memory_mode_section(db),
    }
    if table_exists(db, "memory_items"):
        report["memory_items"] = _memory_items_section(db, now, near_threshold)
    return report


def _numbers(rows, key: str) -> list[float]:
    values = []
    for row in rows:
        value = row.get(key)
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            values.append(float(value))
    return values


def _sum(rows, key: str) -> int:
    return int(sum(_numbers(rows, key)))


def telemetry_report(usage_rows: Iterable[dict]) -> dict:
    """Aggregate content-free usage events that describe memory context and extraction."""

    rows = list(usage_rows)
    provenance = [row for row in rows if row.get("operation") == "context.provenance"]
    with_structured = [
        row for row in provenance
        if (row.get("context_structured_items") or 0) > 0
    ]
    with_public = [
        row for row in provenance if (row.get("context_public_items") or 0) > 0
    ]
    shares = [
        float(row["context_structured_memory_chars"]) / float(row["context_chars_total"])
        for row in provenance
        if isinstance(row.get("context_structured_memory_chars"), (int, float))
        and isinstance(row.get("context_chars_total"), (int, float))
        and row["context_chars_total"] > 0
    ]
    extraction = [row for row in rows if row.get("operation") == "memory.shadow_extraction"]
    completed = [row for row in extraction if row.get("status") == "completed"]
    shared = [row for row in rows if row.get("operation") == "memory.shared_summary_result"]
    timestamps = sorted(str(row.get("at")) for row in provenance if row.get("at"))
    return {
        "context_provenance": {
            "turns": len(provenance),
            "oldest_at": timestamps[0] if timestamps else None,
            "newest_at": timestamps[-1] if timestamps else None,
            "structured_items": _quantiles(_numbers(provenance, "context_structured_items")),
            "structured_memory_chars": _quantiles(
                _numbers(provenance, "context_structured_memory_chars")
            ),
            "structured_share_of_context_chars": _quantiles(shares),
            "turns_with_structured_memory": len(with_structured),
            "public_server_context_turns": len(with_public),
            "public_server_context_rate": _ratio(len(with_public), len(provenance)),
            "public_context_chars_when_present": _quantiles(
                _numbers(with_public, "context_public_chars")
            ),
            "factual_recall_status": dict(Counter(
                str(row.get("factual_recall_status") or "missing") for row in provenance
            )),
        },
        "shadow_extraction": {
            "status": dict(Counter(str(row.get("status") or "missing") for row in extraction)),
            "completed_totals": {
                key: _sum(completed, key)
                for key in (
                    "batch_turns", "accepted_items", "stored_items", "duplicate_items",
                    "rejected_items", "proposal_items", "applied_reconciliations",
                    "deferred_reconciliations", "rejected_relations",
                )
            },
        },
        "shared_summary_result": dict(
            Counter(str(row.get("status") or "missing") for row in shared)
        ),
    }


def build_report(
    database_path: str | Path,
    *,
    usage_log_path: str | Path | None = None,
    now: datetime | None = None,
    near_threshold: float = DEFAULT_NEAR_DUPLICATE_THRESHOLD,
) -> dict:
    current = (now or datetime.now(UTC)).astimezone(UTC)
    db = connect_existing(database_path, readonly=True)
    try:
        database = database_report(db, now=current, near_threshold=near_threshold)
    finally:
        db.close()
    telemetry = None
    if usage_log_path:
        reader = TelemetryReader(usage_log_path, None)
        telemetry = {
            "usage_files": reader.source_status()["usage_files"],
            **telemetry_report(reader.snapshot().usage),
        }
    return {
        "report": "hina-memory-report",
        "version": REPORT_VERSION,
        "generated_at": current.isoformat(timespec="seconds"),
        "privacy": "aggregates only: no memory text, names, notes or Discord identifiers",
        "database": database,
        "telemetry": telemetry,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--db",
        default=os.getenv("DATABASE_PATH", "data/hina.sqlite3"),
        help="Runtime SQLite database (default: DATABASE_PATH)",
    )
    parser.add_argument(
        "--usage-log",
        default=os.getenv("USAGE_LOG_PATH", "data/logs/usage.jsonl"),
        help="Usage telemetry JSONL, rotated files included (default: USAGE_LOG_PATH); "
             "empty string to skip",
    )
    parser.add_argument(
        "--near-threshold",
        type=float,
        default=DEFAULT_NEAR_DUPLICATE_THRESHOLD,
        help="Character-bigram Jaccard threshold for near-duplicate counts",
    )
    args = parser.parse_args(argv)
    if not 0 < args.near_threshold <= 1:
        parser.error("--near-threshold must be in (0, 1]")
    usage_log = args.usage_log.strip() or None
    report = build_report(
        args.db,
        usage_log_path=usage_log,
        near_threshold=args.near_threshold,
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

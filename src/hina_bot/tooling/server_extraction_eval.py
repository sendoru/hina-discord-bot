"""Score the guild fixtures through the production extractor and SQLite writer."""
import json
from contextlib import closing
from datetime import UTC, datetime

from hina_bot.ai.server_memory_extraction import extract_server_batch
from hina_bot.core.routing import Scope
from hina_bot.core.store import Store


def _user_id(label):
    return 910000 + int(label.removeprefix("u"))


def _matches(row, expectations):
    for key, expected in expectations.items():
        if key == "match_any":
            if not any(token in row["content"] for token in expected):
                return False
        elif key == "usage_mode_in":
            if row["usage_mode"] not in expected:
                return False
        elif key in ("subject_users", "subject_users_include"):
            ids = set(json.loads(row["subject_user_ids"]))
            if key == "subject_users" and ids != {str(_user_id(label)) for label in expected}:
                return False
            if key == "subject_users_include" and str(_user_id(expected)) not in ids:
                return False
        elif key == "recurrence_prefix":
            if not (row["recurrence"] or "").startswith(expected):
                return False
        elif row[key] != expected:
            return False
    return True


async def run_server_case(worker, case):
    with closing(Store.create(":memory:")) as store:
        for index, turn in enumerate(case["turns"], 1):
            scope = Scope(930001, 920001, _user_id(turn["user_id"]), case["space"] == "public")
            store.add(scope, index, turn["user"], turn.get("hina", ""), name=turn["speaker"],
                      context_provenance={"source_trigger_kind": "explicit"})
            timestamp = datetime.fromisoformat(turn["at"]).astimezone(UTC).strftime("%Y-%m-%d %H:%M:%S")
            with store.db:
                store.db.execute("UPDATE turns SET created_at=? WHERE message_id=?", (timestamp, str(index)))
        await extract_server_batch(worker, store, 930001, 920001, batch_size=1)
        rows = store._server_memories.items(930001)
        failures = [f"missing {rule}" for rule in case.get("expect_items", [])
                    if not any(_matches(row, rule) for row in rows)]
        failures.extend(f"forbidden {rule}" for rule in case.get("forbid_items", [])
                        if any(_matches(row, rule) for row in rows))
        if case.get("expect_no_items") and rows:
            failures.append("expected no items")
        return {"id": case["id"], "status": "failed" if failures else "passed",
                "failures": failures, "items": rows}

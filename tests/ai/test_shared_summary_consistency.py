"""The generated summary must still describe the state approved before await."""

import asyncio
import json
import sqlite3
from types import SimpleNamespace as NS
from unittest.mock import Mock

import pytest

from hina_bot.ai.memory_summary import MemorySummaryMixin
from hina_bot.ai.usage import UsageLogger
from hina_bot.core.routing import Scope
from hina_bot.core.store import Store


class PausedSummary(MemorySummaryMixin):
    def __init__(self):
        self.entered, self.release = asyncio.Event(), asyncio.Event()
        self.settings = NS(summary_every=1, model_routing_mode="fixed", model="fake",
                           memory_output_tokens=4096, gemini_thinking_level="low",
                           memory_routing_smart_threshold=2.0)
        self.usage = NS(routing_event=Mock())
        self.response = NS(status="completed", output_text="generated-secret")

    async def _memory_request(self, *args):
        self.entered.set()
        await self.release.wait()
        return self.response


@pytest.fixture
def stores(tmp_path):
    path = tmp_path / "summary.sqlite3"
    first = Store.create(path, history_turns=3)
    second = Store.open_existing(path, history_turns=3)
    scope = Scope(1, 10, 100, public_at_capture=True)
    with first.db:
        first._conversations.write_shared_call(scope, 101, "name-secret", "source-secret")
    with first.db:
        first._conversations.write_shared_summary(scope, "name-secret", "old-secret", 0)
    try:
        yield first, second, scope
    finally:
        first.close()
        second.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("mutation", [
    "purge_channel_memory", "purge_realm_memory", "purge_all_memory", "forget",
    "source", "summary", "cursor", "name", "delete_summary", "prune",
])
async def test_changed_input_is_rejected_without_resurrection_or_cursor_regression(stores, mutation):
    first, second, scope = stores
    llm = PausedSummary()
    task = asyncio.create_task(llm.summarize_shared(first, scope))
    try:
        await asyncio.wait_for(llm.entered.wait(), 2)
        assert not first.db.in_transaction
        if mutation in {"purge_channel_memory", "purge_realm_memory", "forget"}:
            getattr(second, mutation)(scope)
        elif mutation == "purge_all_memory":
            second.purge_all_memory()
        elif mutation == "prune":
            for number in (102, 103, 104):
                with second.db:
                    second._conversations.write_shared_call(scope, number, "other", "new")
        else:
            with second.db:
                if mutation == "source":
                    second.db.execute("UPDATE shared_calls SET content='edited-secret'")
                elif mutation == "delete_summary":
                    second.db.execute("DELETE FROM shared_summaries")
                elif mutation == "cursor":
                    second.db.execute("UPDATE shared_summaries SET through_id=1")
                else:
                    column = "name" if mutation == "name" else "text"
                    second.db.execute(f"UPDATE shared_summaries SET {column}='new-secret'")
        before = list(second.db.iterdump())
        llm.release.set()
        with pytest.raises(RuntimeError, match="changed"):
            await asyncio.wait_for(task, 2)
        assert list(second.db.iterdump()) == before
        assert not first.db.in_transaction
        event = llm.usage.routing_event.call_args
        assert event.args[0] == "memory.shared_summary_result"
        assert event.kwargs["status"] == "conflict"
        assert "secret" not in str(llm.usage.routing_event.call_args_list)
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
async def test_new_calls_and_unrelated_changes_do_not_discard_valid_work(stores):
    first, second, scope = stores
    llm = PausedSummary()
    task = asyncio.create_task(llm.summarize_shared(first, scope))
    await asyncio.wait_for(llm.entered.wait(), 2)
    try:
        with second.db:
            second._conversations.write_shared_call(scope, 102, "name", "tail")
        second.set_note("unrelated", "keep")
        llm.release.set()
        await task
        assert second.shared_summary(scope) == ("generated-secret", 1)
        assert [row["message_id"] for row in second.pending_shared(scope)] == ["102"]
        assert llm.usage.routing_event.call_args.kwargs["status"] == "committed"
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
async def test_concurrent_summary_cannot_overwrite_the_first_commit(stores):
    first, second, scope = stores
    a, b = PausedSummary(), PausedSummary()
    a.response.output_text, b.response.output_text = "winner", "loser"
    tasks = [asyncio.create_task(a.summarize_shared(first, scope)),
             asyncio.create_task(b.summarize_shared(second, scope))]
    try:
        await asyncio.wait_for(asyncio.gather(a.entered.wait(), b.entered.wait()), 2)
        a.release.set()
        await tasks[0]
        b.release.set()
        with pytest.raises(RuntimeError, match="changed"):
            await tasks[1]
        assert second.shared_summary(scope) == ("winner", 1)
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


def test_guarded_save_failure_rolls_back_and_is_invisible_to_other_connections(stores):
    first, second, scope = stores
    expected, pending = first.shared_summary_snapshot(scope)

    def observe_and_abort():
        assert second.shared_summary(scope) == ("old-secret", 0)
        raise RuntimeError("abort write")

    first.db.create_function("abort_summary", 0, observe_and_abort)
    first.db.execute("""CREATE TRIGGER reject_summary AFTER INSERT ON shared_summaries
                        BEGIN SELECT abort_summary(); END""")
    with pytest.raises(sqlite3.OperationalError):
        first.commit_shared_summary(scope, pending, expected_summary=expected, text="new")
    assert second.shared_summary(scope) == ("old-secret", 0)
    assert not first.db.in_transaction


def test_snapshot_and_save_never_commit_an_existing_transaction(stores):
    first, second, scope = stores
    expected, pending = first.shared_summary_snapshot(scope)
    first.db.execute("BEGIN IMMEDIATE")
    first.db.execute("INSERT INTO notes VALUES ('uncommitted','secret')")
    try:
        for operation in (
            lambda: first.shared_summary_snapshot(scope),
            lambda: first.commit_shared_summary(scope, pending, expected_summary=expected, text="new"),
        ):
            with pytest.raises(RuntimeError, match="transaction"):
                operation()
            assert first.db.in_transaction
            assert second.note("uncommitted") == ""
    finally:
        first.db.rollback()


def test_input_reads_share_one_snapshot_even_when_another_writer_commits(stores, monkeypatch):
    first, second, scope = stores
    original = first._conversations.shared_summary_row

    def read_then_change(scope):
        row = original(scope)
        with second.db:
            second.db.execute("UPDATE shared_calls SET content='changed'")
            second.db.execute("UPDATE shared_summaries SET text='changed'")
        return row

    monkeypatch.setattr(first._conversations, "shared_summary_row", read_then_change)
    expected, pending = first.shared_summary_snapshot(scope)
    assert expected["text"] == "old-secret"
    assert pending[0]["content"] == "source-secret"
    assert not first.db.in_transaction
    monkeypatch.undo()
    with pytest.raises(RuntimeError, match="changed"):
        first.commit_shared_summary(scope, pending, expected_summary=expected, text="stale")


def test_commit_failure_rolls_back_and_the_same_input_can_be_retried(tmp_path):
    class Connection(sqlite3.Connection):
        fail_commit = False

        def commit(self):
            if self.fail_commit:
                raise sqlite3.OperationalError("synthetic commit failure")
            super().commit()

    path = tmp_path / "commit.sqlite3"
    Store.create(path).close()
    connection = sqlite3.connect(path, factory=Connection)
    connection.row_factory = sqlite3.Row
    first, second = Store(connection), Store.open_existing(path)
    scope = Scope(1, 10, 100, True)
    try:
        with first.db:
            first._conversations.write_shared_call(scope, 101, "name", "source")
        expected, pending = first.shared_summary_snapshot(scope)
        assert expected is None
        connection.fail_commit = True
        with pytest.raises(sqlite3.OperationalError, match="commit failure"):
            first.commit_shared_summary(scope, pending, expected_summary=expected, text="new")
        assert second.shared_summary(scope) == ("", 0)
        assert not first.db.in_transaction
        connection.fail_commit = False
        first.commit_shared_summary(scope, pending, expected_summary=expected, text="new")
        assert second.shared_summary(scope) == ("new", 1)
    finally:
        first.close()
        second.close()


@pytest.mark.asyncio
async def test_storage_failure_never_emits_committed_telemetry(stores):
    first, second, scope = stores
    first.db.execute("""CREATE TRIGGER fail_summary BEFORE INSERT ON shared_summaries
                        BEGIN SELECT RAISE(ABORT, 'private-secret'); END""")
    llm = PausedSummary()
    llm.release.set()
    with pytest.raises(sqlite3.IntegrityError):
        await llm.summarize_shared(first, scope)
    assert second.shared_summary(scope) == ("old-secret", 0)
    assert llm.usage.routing_event.call_args.kwargs["status"] == "failed"
    assert llm.usage.routing_event.call_args.kwargs["memory_result_reason"] == "storage_failed"
    assert "secret" not in str(llm.usage.routing_event.call_args_list)


@pytest.mark.asyncio
@pytest.mark.parametrize("status,text", [("incomplete", "unfinished"), ("completed", "  ")])
async def test_incomplete_output_is_deferred_instead_of_reported_as_saved(stores, status, text):
    first, second, scope = stores
    llm = PausedSummary()
    llm.response = NS(status=status, output_text=text)
    llm.release.set()
    with pytest.raises(RuntimeError):
        await llm.summarize_shared(first, scope)
    assert second.shared_summary(scope) == ("old-secret", 0)
    assert llm.usage.routing_event.call_args.kwargs["status"] == "deferred"


@pytest.mark.asyncio
async def test_cancelled_request_keeps_pending_and_reports_cancellation(stores):
    first, second, scope = stores
    llm = PausedSummary()
    task = asyncio.create_task(llm.summarize_shared(first, scope))
    await asyncio.wait_for(llm.entered.wait(), 2)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert second.shared_summary(scope) == ("old-secret", 0)
    assert llm.usage.routing_event.call_args.kwargs["status"] == "cancelled"


def test_summary_result_telemetry_uses_allowed_metadata_without_raw_content(tmp_path):
    path = tmp_path / "usage.jsonl"
    logger = UsageLogger(str(path))
    try:
        logger.routing_event("memory.shared_summary_result", status="conflict",
                             memory_result_reason="source_changed", source="secret",
                             output_text="secret")
    finally:
        logger.close()
    row = json.loads(path.read_text())
    assert row["memory_result_reason"] == "source_changed"
    assert "secret" not in path.read_text()


@pytest.mark.asyncio
async def test_personal_off_while_shared_summary_inflight_prevents_commit(stores):
    first, second, scope = stores
    llm = PausedSummary()
    task = asyncio.create_task(llm.summarize_shared(first, scope))
    try:
        await asyncio.wait_for(llm.entered.wait(), 2)
        second.set_user_memory_mode(scope.user_id, "off")
        llm.release.set()
        assert await asyncio.wait_for(task, 2) is False
        assert second.shared_summary(scope) == ("old-secret", 0)
        assert llm.usage.routing_event.call_args.kwargs["status"] == "deferred"
    finally:
        llm.release.set()
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)

import json

import pytest

from hina_bot.core.routing import Scope
from hina_bot.core.store import Store
from hina_bot.tooling.reset_conversation import _archive_debug_rows


@pytest.fixture
def store():
    store = Store.create(':memory:')
    yield store
    store.close()


def test_exact_channel_and_dm_owner_isolation(store):
    scopes = [Scope(1, 10, 100), Scope(1, 10, 200), Scope(1, 11, 100),
              Scope(2, 10, 100), Scope(None, 10, 100), Scope(None, 10, 200)]
    for index, scope in enumerate(scopes, 1):
        store.add(scope, index, 'user', 'assistant')
    assert [r['message_id'] for r in store.chat_history_records(scopes[0], range(1, 7))] == [
        '1', '2',
    ]
    assert [r['message_id'] for r in store.chat_history_records(scopes[4], range(1, 7))] == ['5']


def test_archive_is_optional_read_only_and_projects_only_safe_fields(store):
    scope = Scope(1, 10, 100)
    provenance = {'private_prompt': 'secret', 'history_lookup': {'count': 1},
                  'assistant_message_ids': ['50']}
    store.add(scope, 1, 'user', 'assistant', context_provenance=provenance,
              memory_context=[{'content': 'private memory'}])
    assert store.chat_history_records(scope, [1])[0]['source'] == 'sqlite'
    with store.db:
        _archive_debug_rows(store.db, 1)
        store.db.execute('DELETE FROM turns')
    changes = store.db.total_changes
    result = store.chat_history_records(scope, [1])
    assert len(result) == 1
    assert result[0]['source'] == 'archive'
    assert result[0]['history_derived'] is True
    assert result[0]['assistant_message_ids'] == ['50']
    assert 'secret' not in json.dumps(result)
    assert 'private memory' not in json.dumps(result)
    assert store.history(scope) == []
    assert store.db.total_changes == changes


def test_pruned_and_failed_turns_are_not_invented_or_restored(store):
    scope = Scope(None, 10, 100)
    store.history_turns = 1
    store.add(scope, 1, 'gone', 'gone')
    store.add(scope, 2, 'retained', 'retained')
    store.add_failed_turn(scope, 3, 'failed', status='failed', stage='answer',
                          reply_delivered=False)
    with store.db:
        _archive_debug_rows(store.db, 1)
        store.db.execute('DELETE FROM turns')
    assert [r['message_id'] for r in store.chat_history_records(scope, [1, 2, 3])] == ['2']


@pytest.mark.parametrize('ids', [[0], [-1], ['1 OR 1'], ['１２'], [2**63], list(range(1, 102))])
def test_lookup_rejects_unbounded_or_invalid_ids(store, ids):
    with pytest.raises(ValueError):
        store.chat_history_records(Scope(None, 10, 100), ids)


def test_lookup_handles_legacy_malformed_provenance_without_exposing_it(store):
    scope = Scope(None, 10, 100)
    store.add(scope, 1, 'user', 'reply')
    with store.db:
        store.db.execute("UPDATE turns SET context_provenance='[]'")
    row = store.chat_history_records(scope, [1, 1])[0]
    assert row['history_derived'] is False
    assert row['assistant_message_ids'] == []
    assert store.chat_history_records(scope, []) == []

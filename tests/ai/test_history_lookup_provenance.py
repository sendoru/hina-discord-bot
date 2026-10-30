import json
from datetime import UTC, datetime
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock

import pytest

from hina_bot.ai.dialogue_context import project_dialogue_context
from hina_bot.ai.memory_extraction import build_shadow_evidence_context, build_shadow_turns
from hina_bot.ai.request_assembly import RequestAssembler
from hina_bot.core.config import Settings
from hina_bot.core.memory_context import CURRENT_CONTEXT_PROVENANCE, history_lookup_provenance
from hina_bot.core.routing import Scope
from hina_bot.core.store import Store
from hina_bot.discord.target_recent import TargetAwareRecentMessages
from hina_bot.discord.turn_provenance import CURRENT_TURN_PROVENANCE
from hina_bot.discord.web_bot import HinaClient


def test_restart_preserves_provenance_without_copying_search_payload_into_memory(tmp_path):
    path = tmp_path / 'history.sqlite3'
    scope = Scope(None, 10, 100)
    history = {'message_ids': ['10'], 'sources': ['discord'], 'contains_before_reset': True}
    store = Store.create(path)
    store.add(scope, 100, '이전에 뭐라고 했어?', '과거 자료를 설명한 답변',
              context_provenance={'history_lookup': history, 'assistant_message_ids': ['101']})
    store.close()
    store = Store.open_existing(path)
    try:
        pending = store.history(scope)
        assert history_lookup_provenance(pending[0]) == history
        assert store.chat_history_reply_metadata(scope, [101]) == {'101': history}
        assert store.chat_history_reply_metadata(Scope(None, 11, 100), [101]) == {}
        assert store.chat_history_reply_metadata(Scope(None, 10, 200), [101]) == {}
        turns, source_ids, _, _ = build_shadow_turns(pending, include_replies=True)
        assert source_ids == {'100'}
        assert 'hina' not in turns[0]
        assert '과거 자료' not in json.dumps(turns, ensure_ascii=False)
        evidence, _ = build_shadow_evidence_context(pending)
        assert evidence[0]['hina'] == ''
        # The real current user question is preserved; no extraction cursor is rewound.
        assert turns[0]['user'] == '이전에 뭐라고 했어?'
        raw, _ = RequestAssembler._dm_conversation_history(store, scope, 12000)
        context = project_dialogue_context({'current_speaker': {'user_id': '100'},
                                            'conversation_history': raw})
        reply = context['conversation_history'][0]['assistant_reply']
        assert reply['provenance_class'] == 'reference_derived'
        assert reply['content'] == '과거 자료를 설명한 답변'
    finally:
        store.close()


def test_server_recent_and_live_buffer_mark_answer_without_inserting_old_messages():
    store = Store.create(':memory:')
    scope = Scope(1, 10, 100)
    history = {'message_ids': ['10'], 'sources': ['archive']}
    token = CURRENT_TURN_PROVENANCE.set({'history_lookup': history})
    try:
        recent = TargetAwareRecentMessages(store=store)
        recent.add(scope, 101, '히나', '과거 인용에 대한 현재 답변', role='assistant',
                   author_user_id=99, reply_target_user_id=100, capture_turn_provenance=True)
        rows = recent.context(scope, 200)
        assert len(rows) == 1
        assert rows[0]['provenance_class'] == 'reference_derived'
        assert rows[0]['message_id'] == 101
        assert '10' not in [str(row['message_id']) for row in rows]
        store.record_completed_turn(scope, 100, '과거 질문', '과거 인용에 대한 현재 답변',
                                    context_provenance={'history_lookup': history})
        records = RequestAssembler._server_recent_conversation(store, scope, [])
        projected = project_dialogue_context({'current_speaker': {'user_id': '100'},
                                              'personal_recent_conversation': records})
        assert projected['personal_recent_conversation'][0]['assistant_reply'][
            'provenance_class'] == 'reference_derived'
        # Public summary inputs contain the current user call, not tool output or assistant text.
        assert [row['content'] for row in store.pending_shared(scope)] == []
    finally:
        CURRENT_TURN_PROVENANCE.reset(token)
        store.close()


@pytest.mark.asyncio
async def test_restarted_discord_hydration_recovers_derived_reply_metadata(tmp_path):
    path = tmp_path / 'history.sqlite3'
    scope = Scope(1, 10, 100)
    store = Store.create(path)
    history = {'message_ids': ['1'], 'sources': ['discord'], 'contains_before_reset': True}
    store.add(scope, 100, '과거를 검색해 줘', '그때의 답변은 이랬어',
              context_provenance={'history_lookup': history, 'assistant_message_ids': ['101']})
    store.close()
    store = Store.open_existing(path)
    bot = HinaClient(Settings(discord_token='test', openai_api_key='test'),
                     store=store, llm=NS(close=AsyncMock()))
    bot._connection.user = NS(id=99, display_name='히나')
    now = datetime.now(UTC)

    def history_api(**kwargs):
        async def rows():
            # The user request is outside this page; matching uses stored delivery IDs.
            yield NS(id=101, content='그때의 답변은 이랬어', created_at=now,
                     author=NS(id=99, bot=True, display_name='히나'),
                     webhook_id=None, attachments=[], stickers=[])
        return rows()
    try:
        current = NS(id=200, created_at=now, channel=NS(history=history_api))
        await bot.hydrate_recent_history(current, scope)
        rows = bot.recent.context(scope, 200)
        assert len(rows) == 1
        assert rows[0]['provenance_class'] == 'reference_derived'
        assert rows[0]['history_lookup'] == history
        assert '1' not in [str(row['message_id']) for row in rows]
    finally:
        await bot.close()


def test_non_history_turns_keep_existing_extraction_behavior():
    store = Store.create(':memory:')
    scope = Scope(None, 10, 100)
    try:
        store.add(scope, 1, '새로운 현재 사실', '일반 답변')
        turns, _, _, _ = build_shadow_turns(store.history(scope), include_replies=True)
        assert turns[0]['hina'] == '일반 답변'
        assert history_lookup_provenance({'context_provenance': '[]'}) == {}
    finally:
        CURRENT_CONTEXT_PROVENANCE.set(None)
        store.close()

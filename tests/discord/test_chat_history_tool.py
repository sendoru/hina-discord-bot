import json
from datetime import UTC, datetime
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest

from hina_bot.ai.local_tools import (
    CURRENT_LOCAL_TOOLS,
    LocalToolCall,
    LocalToolError,
    LocalToolExecutor,
    LocalToolRegistry,
)
from hina_bot.ai.runtime_llm import LLM
from hina_bot.core.config import Settings
from hina_bot.core.memory_context import CURRENT_CONTEXT_PROVENANCE
from hina_bot.core.routing import Scope
from hina_bot.core.store import Store
from hina_bot.discord.chat_history_tool import ChatHistoryLookup, register_chat_history_tool
from hina_bot.discord.chatlog_capture import set_capture_mode_override
from hina_bot.discord.member_directory import channel_member_tool_registry
from hina_bot.discord.turn_provenance import CURRENT_TURN_PROVENANCE
from hina_bot.tooling.migrate_conversation_reset_epochs import ensure_epoch_schema
from hina_bot.tooling.reset_conversation import _archive_debug_rows


def old(message_id, content='히나야 과거 발언', author_id=100, *, bot=False, reply_to=None):
    return NS(id=message_id, content=content,
              author=NS(id=author_id, bot=bot, display_name=f'user-{author_id}'),
              created_at=datetime.now(UTC), guild=NS(id=1), channel=NS(id=10),
              mentions=[], webhook_id=None,
              reference=NS(message_id=reply_to) if reply_to else None)


@pytest.fixture
def runtime():
    store = Store.create(':memory:')
    channel = MagicMock(spec=discord.TextChannel)
    channel.id = 10
    channel.permissions_for.return_value = NS(view_channel=True, read_message_history=True)
    author = NS(id=100, bot=False, display_name='caller')
    guild = NS(id=1, unavailable=False, me=NS(id=99), get_member=lambda _: author,
               chunked=True, members=[])
    message = NS(id=1000, author=author, guild=guild, channel=channel,
                 created_at=datetime.now(UTC))
    settings = NS(external_context_policy='full', dm_always_reply=False,
                  call_prefixes=('히나야',), always_reply_channel_ids=frozenset())
    messages = []
    calls = []

    def history(**kwargs):
        calls.append(kwargs)

        async def rows():
            before = kwargs['before'].id
            after = kwargs['after'].id if kwargs.get('after') else 0
            eligible = sorted((m for m in messages if after < m.id < before),
                              key=lambda m: m.id, reverse=True)
            for value in eligible[:kwargs['limit']]:
                yield value
        return rows()

    channel.history.side_effect = history
    tokens = (CURRENT_CONTEXT_PROVENANCE.set(None), CURRENT_TURN_PROVENANCE.set(None))
    yield NS(store=store, message=message, settings=settings, messages=messages, calls=calls,
             scope=Scope(1, 10, 100), channel=channel,
             lookup=lambda: ChatHistoryLookup(message, 99, store, settings))
    CURRENT_CONTEXT_PROVENANCE.reset(tokens[0])
    CURRENT_TURN_PROVENANCE.reset(tokens[1])
    store.close()


def reset_cutoff(runtime, cutoff=100):
    with runtime.store.db:
        ensure_epoch_schema(runtime.store.db)
        runtime.store.db.execute(
            'INSERT INTO conversation_reset_epochs(reset_at,cutoff_message_id,backup_sha256) '
            'VALUES (?,?,?)', (datetime.now(UTC).isoformat(), cutoff, 'test'),
        )


@pytest.mark.asyncio
async def test_schema_registration_does_not_read_history_and_coexists_with_members(runtime):
    registry = LocalToolRegistry()
    channel_member_tool_registry(runtime.message, 99, registry=registry)
    register_chat_history_tool(registry, runtime.message, 99, runtime.store, runtime.settings)
    assert {s['name'] for s in registry.schemas()} == {
        'get_chat_history', 'get_current_channel_members',
    }
    assert runtime.calls == []
    result = await registry.execute(LocalToolCall('one', 'get_current_channel_members', {}))
    assert not result.is_error
    assert runtime.calls == []


@pytest.mark.asyncio
async def test_default_cutoff_and_direct_jump_before_reset_with_discord_only(runtime):
    reset_cutoff(runtime)
    runtime.messages.extend([old(90), old(150), old(1000), old(1001)])
    lookup = runtime.lookup()
    recent = await lookup({})
    assert [m['message_id'] for m in recent['messages']] == ['150']
    assert runtime.store.history(runtime.scope) == []
    past = await lookup({'include_before_reset': True, 'before_message_id': '100'})
    assert [m['message_id'] for m in past['messages']] == ['90']
    assert past['messages'][0]['source'] == 'discord'
    assert past['messages'][0]['before_reset'] is True
    assert runtime.store.history(runtime.scope) == []
    assert runtime.store.latest_conversation_reset_cutoff() == 100


@pytest.mark.asyncio
async def test_past_does_not_require_failed_recent_lookup(runtime):
    reset_cutoff(runtime)
    runtime.messages.append(old(90))
    result = await runtime.lookup()({'include_before_reset': True})
    assert result['range']['contains_before_reset'] is True
    assert len(runtime.calls) == 1


@pytest.mark.asyncio
async def test_archive_only_verified_utterance_and_live_edits_win(runtime):
    for message_id in (80, 81, 82):
        runtime.store.add(runtime.scope, message_id, f'original-{message_id}', 'private reply',
                          memory_context=[{'content': 'secret memory'}],
                          context_provenance={'private_prompt': 'secret prompt'})
    with runtime.store.db:
        _archive_debug_rows(runtime.store.db, 1)
        runtime.store.db.execute('DELETE FROM turns')
    runtime.messages.extend([old(80, 'original-80'), old(81, 'edited')])
    result = await runtime.lookup()({'include_before_reset': True})
    assert [(m['message_id'], m['source']) for m in result['messages']] == [
        ('80', 'archive'), ('81', 'discord'),
    ]
    assert result['messages'][1]['content'] == 'edited'
    encoded = json.dumps(result)
    assert 'secret' not in encoded and 'private reply' not in encoded
    assert '82' not in [m['message_id'] for m in result['messages']]


@pytest.mark.asyncio
@pytest.mark.parametrize('policy,capture', [('full', 'direct'), ('bot_interactions_only', 'all')])
async def test_direct_policy_requires_evidence_even_for_other_users_and_bot_replies(
    runtime, policy, capture,
):
    runtime.settings.external_context_policy = policy
    set_capture_mode_override(runtime.store, runtime.scope.channel, capture)
    runtime.messages.extend([
        old(10, 'ordinary third party', 200), old(11, '히나야 third party call', 200),
        old(12, 'verified answer', 99, bot=True, reply_to=11),
        old(13, 'unverified answer', 99, bot=True),
        old(14, 'other bot', 201, bot=True),
    ])
    # Presence in SQLite is not direct-call evidence.
    runtime.store.add(Scope(1, 10, 200), 10, 'ordinary third party', 'not permission')
    result = await runtime.lookup()({})
    assert [m['message_id'] for m in result['messages']] == ['11', '12']
    assert result['messages'][0]['author_user_id'] == '200'


@pytest.mark.asyncio
async def test_sqlite_pairing_uses_actual_delivered_ids_and_discord_timestamps(runtime):
    runtime.settings.external_context_policy = 'bot_interactions_only'
    runtime.store.add(runtime.scope, 10, '히나야 질문', '답변', context_provenance={
        'assistant_message_ids': ['11', '12'],
    })
    runtime.messages.extend([old(10, '히나야 질문'), old(11, '답변', 99, bot=True),
                             old(12, '추가 답변', 99, bot=True)])
    result = await runtime.lookup()({})
    assert [m['message_id'] for m in result['messages']] == ['10', '11', '12']
    assert [m['source_turn_message_id'] for m in result['messages']] == ['10', '10', '10']
    assert result['messages'][0]['source'] == 'sqlite'
    assert result['messages'][1]['timestamp_basis'] == 'discord_message'
    assert result['messages'][1]['at'] == runtime.messages[1].created_at.isoformat()


@pytest.mark.asyncio
async def test_chatlog_off_blocks_all_reads(runtime):
    runtime.store.set_chat_log_mode_override(runtime.scope.channel, 'off')
    with pytest.raises(LocalToolError, match='history_disabled'):
        await runtime.lookup()({})
    assert runtime.calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize('member_id', [100, 99])
@pytest.mark.parametrize('permission', ['view_channel', 'read_message_history'])
async def test_caller_and_bot_permissions_fail_closed(runtime, member_id, permission):
    def permissions(member):
        return NS(**{p: not (member.id == member_id and p == permission)
                     for p in ('view_channel', 'read_message_history')})
    runtime.channel.permissions_for.side_effect = permissions
    with pytest.raises(LocalToolError, match='permission_denied'):
        await runtime.lookup()({})
    assert runtime.calls == []


@pytest.mark.asyncio
async def test_permission_or_capture_change_during_io_is_checked_before_return(runtime):
    runtime.messages.append(old(10, 'passive text', 200))
    original = runtime.channel.history.side_effect

    def history(**kwargs):
        async def changed():
            async for value in original(**kwargs):
                yield value
            set_capture_mode_override(runtime.store, runtime.scope.channel, 'direct')
        return changed()
    runtime.channel.history.side_effect = history
    assert (await runtime.lookup()({}))['messages'] == []

    def denied(**kwargs):
        async def changed():
            async for value in original(**kwargs):
                yield value
            runtime.channel.permissions_for.return_value.view_channel = False
        return changed()
    runtime.channel.history.side_effect = denied
    with pytest.raises(LocalToolError, match='permission_denied'):
        await runtime.lookup()({})


@pytest.mark.asyncio
async def test_dm_owner_and_recipient_are_bound(runtime):
    runtime.message.guild = None
    channel = MagicMock(spec=discord.DMChannel)
    channel.id = 10
    channel.recipient = runtime.message.author
    channel.history.side_effect = runtime.channel.history.side_effect
    runtime.message.channel = channel
    for message in [old(10), old(11, 'another DM owner', 200)]:
        message.guild = None
        runtime.messages.append(message)
    registry = LocalToolRegistry()
    register_chat_history_tool(registry, runtime.message, 99, runtime.store, runtime.settings)
    result = await registry.execute(LocalToolCall('dm', 'get_chat_history', {}))
    assert [m['message_id'] for m in result.output['messages']] == ['10']
    channel.recipient = NS(id=200)
    with pytest.raises(LocalToolError, match='permission_denied'):
        await runtime.lookup()({})


@pytest.mark.asyncio
async def test_keyword_empty_pages_advance_and_cursors_are_request_bound(runtime):
    runtime.messages.extend(old(i, 'needle' if i == 50 else 'hay') for i in range(1, 151))
    lookup = runtime.lookup()
    first = await lookup({'query': 'needle'})
    assert first['messages'] == [] and first['has_more']
    second = await lookup({'cursor': first['next_cursor']})
    assert [m['message_id'] for m in second['messages']] == ['50']
    assert second['has_more'] is False
    with pytest.raises(LocalToolError, match='invalid_cursor'):
        await runtime.lookup()({'cursor': first['next_cursor']})


@pytest.mark.asyncio
async def test_result_page_boundaries_do_not_skip_or_duplicate_matches(runtime):
    runtime.messages.extend(old(i) for i in range(1, 31))
    lookup = runtime.lookup()
    first = await lookup({})
    second = await lookup({'cursor': first['next_cursor']})
    third = await lookup({'cursor': second['next_cursor']})
    ids = [m['message_id'] for r in (first, second, third) for m in r['messages']]
    assert len(ids) == len(set(ids)) == 30
    assert len(first['messages']) == len(second['messages']) == 12
    with pytest.raises(LocalToolError, match='budget_exhausted'):
        await lookup({})


@pytest.mark.asyncio
async def test_truncation_and_aggregate_character_budget(runtime):
    runtime.messages.extend(old(i, 'x' * 5000) for i in range(1, 10))
    lookup = runtime.lookup()
    first = await lookup({})
    assert sum(len(m['content']) for m in first['messages']) == 6000
    assert all(m['truncated'] for m in first['messages'])
    await lookup({'cursor': first['next_cursor']})
    with pytest.raises(LocalToolError, match='budget_exhausted'):
        await lookup({})


@pytest.mark.asyncio
@pytest.mark.parametrize('arguments', [
    {'channel_id': '20'}, {'user_id': '200'}, {'include_before_reset': 1}, {'query': []},
    {'query': 'x' * 201}, {'before_message_id': '-1'}, {'before_message_id': '１２'},
    {'before_message_id': str(2**63)}, {'cursor': 'unknown'},
])
async def test_untrusted_arguments_do_not_read_history(runtime, arguments):
    with pytest.raises(LocalToolError):
        await runtime.lookup()(arguments)
    assert runtime.calls == []


@pytest.mark.asyncio
async def test_source_failure_is_distinct_from_empty_and_partial_results(runtime):
    def unavailable(**kwargs):
        async def rows():
            raise TimeoutError
            yield
        return rows()
    runtime.channel.history.side_effect = unavailable
    with pytest.raises(LocalToolError, match='source_unavailable'):
        await runtime.lookup()({})

    def partial(**kwargs):
        async def rows():
            yield old(10)
            raise TimeoutError
        return rows()
    runtime.channel.history.side_effect = partial
    result = await runtime.lookup()({})
    assert result['status'] == 'partial' and len(result['messages']) == 1


@pytest.mark.asyncio
async def test_multiple_calls_in_one_round_share_budget_and_return_safe_errors(runtime):
    registry = LocalToolRegistry()
    register_chat_history_tool(registry, runtime.message, 99, runtime.store, runtime.settings)
    requests = []

    async def send(request):
        requests.append(request)
        if len(requests) == 1:
            return NS(output=[{'type': 'function_call', 'call_id': f'call-{i}',
                               'name': 'get_chat_history', 'arguments': '{}'} for i in range(4)])
        return NS(output=[])
    result = await LocalToolExecutor(registry).run(send, {'input': [], 'tool_choice': 'auto'})
    assert result.local_calls == 4
    assert len(runtime.calls) == 3
    assert 'budget_exhausted' in requests[-1]['input'][-1]['output']


@pytest.mark.asyncio
async def test_provenance_and_telemetry_do_not_copy_original_content(runtime):
    runtime.messages.append(old(10, 'private past content'))
    trace = {'origin_request': {'message_id': '1000'}}
    CURRENT_TURN_PROVENANCE.set(trace)
    events = NS(emit=MagicMock())
    lookup = ChatHistoryLookup(runtime.message, 99, runtime.store, runtime.settings, events=events)
    await lookup({})
    snapshot = CURRENT_CONTEXT_PROVENANCE.get()
    assert snapshot['history_lookup']['message_ids'] == ['10']
    assert trace['history_lookup'] == snapshot['history_lookup']
    assert 'private past content' not in json.dumps(snapshot)
    assert 'private past content' not in str(events.emit.call_args)
    assert runtime.store.history(runtime.scope) == []


@pytest.mark.asyncio
@pytest.mark.parametrize('use_tool', [False, True])
async def test_gemini_answer_requests_history_only_after_model_function_call(runtime, use_tool):
    runtime.messages.append(old(10, 'private past content'))
    registry = LocalToolRegistry()
    register_chat_history_tool(registry, runtime.message, 99, runtime.store, runtime.settings)
    client = NS(provider_name='gemini', responses=NS(), close=AsyncMock())
    llm = LLM(Settings(discord_token='test', provider='gemini', model='gemini-3-flash-preview',
                       model_routing_mode='fixed', routing_classifier_mode='off',
                       chat_web_search=False), client=client)
    responses = [NS(status='completed', output_text='확인했어.', output=[])]
    if use_tool:
        responses.insert(0, NS(status='completed', output_text='', output=[{
            'type': 'function_call', 'call_id': 'history-1', 'name': 'get_chat_history',
            'arguments': '{}',
        }]))
    llm.usage.request = AsyncMock(side_effect=responses)
    token = CURRENT_LOCAL_TOOLS.set(registry)
    try:
        result = await llm.answer(runtime.store, runtime.scope, 'caller', '전에 무슨 말을 했어?',
                                  use_memory=False)
        assert result == '확인했어.'
        calls = llm.usage.request.await_args_list
        assert len(calls) == (2 if use_tool else 1)
        assert calls[0].kwargs['tool_choice'] == 'auto'
        assert 'private past content' not in json.dumps(calls[0].kwargs, ensure_ascii=False)
        assert len(runtime.calls) == (1 if use_tool else 0)
        if use_tool:
            payload = json.loads(calls[-1].kwargs['input'][-1]['output'])
            assert payload['result']['messages'][0]['content'] == 'private past content'
        assert runtime.store.history(runtime.scope) == []
    finally:
        CURRENT_LOCAL_TOOLS.reset(token)
        await llm.close()

import json
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock

import pytest

from hina_bot.ai.providers import ProviderAPIError
from hina_bot.usage import UsageLogger


def response(input_tokens, output_tokens, *, cached=0, reasoning=0, output=None):
    return NS(status='completed', usage=NS(
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        total_tokens=input_tokens + output_tokens,
        input_tokens_details=NS(cached_tokens=cached),
        output_tokens_details=NS(reasoning_tokens=reasoning)), output=output or [])


@pytest.mark.asyncio
async def test_usage_success_and_error_do_not_log_content(tmp_path):
    path = tmp_path / 'usage.jsonl'
    logger = UsageLogger(str(path))
    first_response = response(100, 20, cached=50, reasoning=5)
    client = NS(responses=NS(create=AsyncMock(return_value=first_response)))
    assert await logger.request(client, 'answer', model='test', input='secret') is first_response
    client.responses.create.side_effect = ValueError('secret exception')
    with pytest.raises(ValueError):
        await logger.request(client, 'summarize', model='test', input='secret')
    logger.close()
    text = path.read_text()
    assert 'secret' not in text
    first, second = map(json.loads, text.splitlines())
    assert first['total_tokens'] == 120
    assert first['cached_tokens'] == 50
    assert first['web_search_calls'] == 0
    assert first['web_search_used'] is False
    assert second['error_type'] == 'ValueError'
    assert 'total_tokens' not in second


@pytest.mark.asyncio
async def test_provider_error_logs_safe_diagnostics_without_request_content(tmp_path):
    path = tmp_path / 'usage.jsonl'
    logger = UsageLogger(str(path))
    error = ProviderAPIError(
        'gemini', 400, code='INVALID_ARGUMENT', message='generation_config.foo is invalid')
    client = NS(responses=NS(create=AsyncMock(side_effect=error)))

    with pytest.raises(ProviderAPIError):
        await logger.request(client, 'answer', model='gemini-test', input='secret user message')
    logger.close()

    text = path.read_text()
    assert 'secret user message' not in text
    row = json.loads(text)
    assert row['error_type'] == 'ProviderAPIError'
    assert row['provider'] == 'gemini'
    assert row['http_status'] == 400
    assert row['provider_error_code'] == 'INVALID_ARGUMENT'
    assert row['provider_error_message'] == 'generation_config.foo is invalid'


@pytest.mark.asyncio
async def test_missing_usage_is_unknown(tmp_path):
    path = tmp_path / 'usage.jsonl'
    logger = UsageLogger(str(path))
    incomplete = NS(
        status='incomplete', output=[], _hina_error_codes=['budget_exceeded'], usage=None)
    client = NS(responses=NS(create=AsyncMock(return_value=incomplete)))
    await logger.request(client, 'answer', model='test')
    logger.close()
    row = json.loads(path.read_text())
    assert row['input_tokens'] is None
    assert row['status'] == 'incomplete'
    assert row['response_error_codes'] == ['budget_exceeded']


@pytest.mark.asyncio
async def test_usage_preserves_caller_web_search_configuration_and_logs_it(tmp_path):
    path = tmp_path / 'usage.jsonl'
    logger = UsageLogger(str(path))
    web_response = response(
        100, 20,
        output=[NS(type='web_search_call'), NS(type='message')],
    )
    client = NS(responses=NS(create=AsyncMock(return_value=web_response)))

    await logger.request(
        client,
        'answer',
        model='test',
        instructions='caller policy',
        input='secret user message',
        tools=[{'type': 'web_search'}],
        tool_choice='required',
    )
    logger.close()

    kwargs = client.responses.create.await_args.kwargs
    assert kwargs['tools'] == [{'type': 'web_search'}]
    assert kwargs['tool_choice'] == 'required'
    assert kwargs['instructions'] == 'caller policy'

    row = json.loads(path.read_text())
    assert row['web_search_calls'] == 1
    assert row['web_search_used'] is True
    assert 'secret user message' not in path.read_text()


@pytest.mark.asyncio
async def test_usage_logger_does_not_add_web_search_by_itself(tmp_path):
    path = tmp_path / 'usage.jsonl'
    logger = UsageLogger(str(path))
    client = NS(responses=NS(create=AsyncMock(return_value=response(10, 5))))

    await logger.request(client, 'answer', model='test', instructions='base', input='secret')
    logger.close()

    kwargs = client.responses.create.await_args.kwargs
    assert 'tools' not in kwargs
    assert 'tool_choice' not in kwargs
    assert kwargs['instructions'] == 'base'


@pytest.mark.asyncio
async def test_discord_exchange_aggregates_answer_and_summary_calls(tmp_path):
    path = tmp_path / 'usage.jsonl'
    logger = UsageLogger(str(path))
    client = NS(responses=NS(create=AsyncMock(side_effect=[
        response(1000, 100, cached=400, reasoning=20,
                 output=[NS(type='web_search_call'), NS(type='message')]),
        response(300, 50, cached=100),
        response(200, 40),
    ])))

    with logger.exchange('guild'):
        await logger.request(client, 'answer', model='chat-model', input='secret user message')
        await logger.request(client, 'summarize', model='memory-model', input='secret memory')
        await logger.request(client, 'summarize_shared', model='memory-model', input='secret shared')
    logger.close()

    detail_rows = [json.loads(line) for line in path.read_text().splitlines()]
    assert len(detail_rows) == 3

    exchange_path = tmp_path / 'discord-usage.jsonl'
    text = exchange_path.read_text()
    assert 'secret' not in text
    row = json.loads(text)
    assert row['scope'] == 'guild'
    assert row['status'] == 'completed'
    assert row['calls'] == 3
    assert row['failed_calls'] == 0
    assert row['input_tokens'] == 1500
    assert row['output_tokens'] == 190
    assert row['total_tokens'] == 1690
    assert row['cached_tokens'] == 500
    assert row['reasoning_tokens'] == 20
    assert row['web_search_calls'] == 1
    assert row['usage_complete'] is True
    assert row['models'] == ['chat-model', 'memory-model']
    assert row['operations']['answer']['total_tokens'] == 1100
    assert row['operations']['answer']['web_search_calls'] == 1
    assert row['operations']['summarize']['total_tokens'] == 350
    assert row['operations']['summarize_shared']['total_tokens'] == 240


@pytest.mark.asyncio
async def test_discord_exchange_records_failed_api_call_without_content(tmp_path):
    path = tmp_path / 'usage.jsonl'
    logger = UsageLogger(str(path))
    client = NS(responses=NS(create=AsyncMock(side_effect=RuntimeError('secret failure'))))

    with pytest.raises(RuntimeError), logger.exchange('dm'):
        await logger.request(client, 'answer', model='test', input='secret')
    logger.close()

    row = json.loads((tmp_path / 'discord-usage.jsonl').read_text())
    assert row['scope'] == 'dm'
    assert row['status'] == 'error'
    assert row['error_type'] == 'RuntimeError'
    assert row['calls'] == 1
    assert row['failed_calls'] == 1
    assert row['usage_complete'] is False
    assert 'secret' not in json.dumps(row)

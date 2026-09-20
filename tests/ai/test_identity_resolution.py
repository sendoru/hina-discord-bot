import json
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock

import pytest

from hina_bot.ai.identity_resolution import (
    SpeakerIdentityCandidate,
    SpeakerIdentityResolver,
    identity_resolution_needed,
    parse_identity_resolution,
)
from hina_bot.core.config import Settings


def candidate(user_id, *names):
    return SpeakerIdentityCandidate(str(user_id), tuple(names))


def test_resolution_parser_never_accepts_invented_user_id():
    candidates = [candidate(200, "sendol")]

    result = parse_identity_resolution(
        '{"status":"resolved","user_id":"999"}',
        candidates,
    )

    assert result.status == "none"
    assert not result.user_id


@pytest.mark.parametrize(
    "text",
    [
        "센돌이 누군지 알아?",
        "루루는 어떤 사람이야?",
        "sendol 성격 어떻게 생각해?",
        "manager_lulu 평판 어때?",
    ],
)
def test_identity_resolution_trigger_covers_targeted_profile_and_history_questions(text):
    assert identity_resolution_needed(text)


@pytest.mark.asyncio
async def test_semantic_resolver_handles_transliteration_without_app_rules():
    response = NS(
        status="completed",
        output_text='{"status":"resolved","user_id":"200"}',
        output=[],
        usage=None,
    )
    client = NS(provider_name="gemini", responses=NS(create=AsyncMock()))
    usage = NS(request=AsyncMock(return_value=response))
    settings = Settings(
        "token",
        "key",
        provider="gemini",
        model="gemini-model",
        fast_model="gemini-fast",
        routing_classifier_timeout_seconds=5,
    )
    resolver = SpeakerIdentityResolver(settings, client, usage)
    candidates = [
        candidate(200, "tag : sendol"),
        candidate(300, "manager_lulu"),
    ]

    result = await resolver.resolve("센돌이 누군지 알아?", candidates)

    assert result.resolved
    assert result.user_id == "200"
    call = usage.request.await_args
    assert call.args[:2] == (client, "identity_resolve")
    assert call.kwargs["model"] == "gemini-fast"
    assert call.kwargs["thinking_level"] == "minimal"
    payload = json.loads(call.kwargs["input"])
    assert payload["request"] == "센돌이 누군지 알아?"
    assert payload["candidates"][0] == {
        "user_id": "200",
        "names": ["tag : sendol"],
    }

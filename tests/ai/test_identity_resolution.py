import json
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock

import pytest

from hina_bot.ai.identity_resolution import (
    SpeakerIdentityCandidate,
    SpeakerIdentityResolver,
    identity_group,
    identity_resolution_needed,
    match_identity_aliases,
    narrow_identity_candidates,
    normalize_identity_reference,
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
        discord_token="key",
        gemini_api_key="token",
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


def test_reference_must_be_copied_from_request_and_group_is_keyed():
    candidates = [candidate(200, "sendol")]
    result = parse_identity_resolution(
        '{"status":"resolved","user_id":"200","reference":"센돌"}',
        candidates,
        request="센돌이 누군지 알아?",
    )
    invalid = parse_identity_resolution(
        '{"status":"resolved","user_id":"200","reference":"invented"}',
        candidates,
        request="센돌이 누군지 알아?",
    )

    assert result.reference == "센돌"
    assert invalid.resolved
    assert invalid.reference == ""
    assert normalize_identity_reference(" Sendol! ") == "sendol"
    assert identity_group(
        "Sendol!",
        guild_id=1,
        secret="deployment-secret",
        kind="reference",
    ) == identity_group(
        " sendol ",
        guild_id=1,
        secret="deployment-secret",
        kind="reference",
    )
    assert identity_group(
        "sendol",
        guild_id=2,
        secret="deployment-secret",
        kind="reference",
    ) != identity_group(
        "sendol",
        guild_id=1,
        secret="deployment-secret",
        kind="reference",
    )


@pytest.mark.parametrize(
    "text,reference",
    [
        ("히나야 2_718281 핑해줘", "2_718281"),
        ("tag : uhe 불러줘", "tag : uhe"),
        ("TAG : UHE가 방금 뭐라고 했어?", "TAG : UHE"),
        ("２_７１８２８１ 불러줘", "２_７１８２８１"),
        ("tag  :  uhe 불러줘", "tag  :  uhe"),
    ],
)
def test_current_alias_match_preserves_exact_original_reference(text, reference):
    result = match_identity_aliases(text, [candidate(200, "tag : uhe", "2_718281")])
    assert result.resolved
    assert result.user_id == "200"
    assert result.reference == reference


@pytest.mark.parametrize(
    "text,alias",
    [
        ("sendolish 불러줘", "sendol"),
        ("xsendol 불러줘", "sendol"),
        ("sendol.foo 불러줘", "sendol"),
        ("other-sendol 불러줘", "sendol"),
        ("a_b 불러줘", "ab"),
        ("ab 불러줘", "a_b"),
        ("아무 말이나 해", "a"),
        ("2026년 이야기", "2026"),
        ("센돌 불러줘", "sendol"),
    ],
)
def test_local_match_rejects_substrings_short_names_and_inferred_identity(text, alias):
    assert not match_identity_aliases(text, [candidate(200, alias)]).resolved


def test_exact_match_checks_collisions_beyond_provider_candidate_limit():
    candidates = [candidate(i, f"unrelated-{i}") for i in range(1, 40)]
    candidates.extend([candidate(100, "duplicate"), candidate(200, "ＤＵＰＬＩＣＡＴＥ")])
    assert match_identity_aliases("duplicate 불러줘", candidates).status == "ambiguous"
    assert match_identity_aliases("unrelated-1과 duplicate 불러줘", candidates).status == "ambiguous"


def test_local_shortlist_supports_phonetic_names_and_preserves_competing_candidates():
    candidates = [candidate(i, f"unrelated-{i}") for i in range(1, 100)]
    candidates.extend([candidate(200, "tag : sendol"), candidate(300, "sendol")])
    assert {row.user_id for row in narrow_identity_candidates("센돌이 누군지 알아?", candidates)} == {
        "200", "300",
    }
    assert narrow_identity_candidates("없는이름 핑해줘", candidates) == ()


@pytest.mark.asyncio
async def test_semantic_resolver_does_not_truncate_away_identity_competitors():
    usage = NS(request=AsyncMock())
    resolver = SpeakerIdentityResolver(
        Settings(discord_token="test"), NS(provider_name="gemini"), usage,
    )
    result = await resolver.resolve(
        "duplicate 불러줘", [candidate(i, "duplicate") for i in range(1, 34)],
    )
    assert result.status == "ambiguous"
    usage.request.assert_not_awaited()


@pytest.mark.asyncio
async def test_semantic_resolver_preserves_validated_reference_span():
    response = NS(
        status="completed",
        output_text='{"status":"resolved","user_id":"200","reference":"센돌"}',
        output=[],
        usage=None,
    )
    client = NS(provider_name="gemini", responses=NS(create=AsyncMock()))
    usage = NS(request=AsyncMock(return_value=response))
    settings = Settings(
        discord_token="key",
        gemini_api_key="token",
        provider="gemini",
        model="gemini-model",
        fast_model="gemini-fast",
        routing_classifier_timeout_seconds=5,
    )
    resolver = SpeakerIdentityResolver(settings, client, usage)

    result = await resolver.resolve(
        "센돌이 누군지 알아?",
        [candidate(200, "sendol")],
    )

    assert result.resolved
    assert result.reference == "센돌"

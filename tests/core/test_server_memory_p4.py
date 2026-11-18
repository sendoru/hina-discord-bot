"""P4 pre-release storage/consent contracts (no LLM or Discord access)."""


import pytest

from hina_bot.core.db_schema import SchemaError
from hina_bot.core.routing import Scope
from hina_bot.core.server_memory_repository import ServerSourceRef
from hina_bot.core.store import Store


@pytest.fixture
def store():
    value = Store.create(":memory:")
    yield value
    value.close()


def source(
    store, *, guild=1, channel=10, user=100, message=1001,
    public=True, direct=True, content="이 서버는 매주 모임을 해요",
    reply="응, 다음 주에도 만나자", history_lookup=False,
):
    scope = Scope(guild, channel, user, public)
    provenance = {"source_trigger_kind": "explicit" if direct else "implicit"}
    if history_lookup:
        provenance["history_lookup"] = {"source": ["old"]}
    store.add(
        scope, message, content, reply, context_provenance=provenance,
    )
    return ServerSourceRef(str(message), "user")


def remember(store, guild=1, message=1001, **kwargs):
    return store.add_server_memory(
        guild, content="서버 모임은 매주 열림",
        evidence="매주 모임",
        sources=[ServerSourceRef(str(message), "user")],
        **kwargs,
    )


def visible(store, *, scope=None, channels=(10,), members=(100, 200), related=()):
    return store.server_memory_candidates(
        scope or Scope(1, 10, 200, True),
        readable_channel_ids=channels,
        member_user_ids=members,
        related_user_ids=related,
    )


def test_strict_schema_v3_is_bootstrapped_and_detects_drift(store):
    assert store.db.execute(
        "SELECT version FROM schema_migrations WHERE component='store' ORDER BY version DESC"
    ).fetchone()[0] == 3
    assert {
        row[0] for row in store.db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'server_memory_%'"
        )
    } == {"server_memory_items", "server_memory_sources", "server_memory_opt_outs"}
    with store.db:
        store.db.execute("ALTER TABLE server_memory_sources ADD COLUMN unexpected TEXT")
    from hina_bot.core.db_schema import require_current

    with pytest.raises(SchemaError):
        require_current(store.db, ("store",))


def test_verified_public_source_is_not_cross_user_personal_memory(store):
    source(store)
    item_id = remember(store)
    rows = visible(store)
    assert [r["id"] for r in rows] == [item_id]
    assert rows[0]["trust"] == "single"
    assert rows[0]["usage_mode"] == "contextual"
    assert not visible(store, scope=Scope(2, 10, 200, True))
    assert not visible(store, channels=())  # No live permission -> no disclosure.
    assert not visible(store, members=())   # No current contributors -> no disclosure.
    assert store.server_memory_candidates(
        Scope(None, 10, 200), readable_channel_ids=[10], member_user_ids=[100]
    ) == []


def test_server_admin_mode_is_separate_from_current_speaker_preference(store):
    source(store)
    remember(store)
    store.set_user_memory_mode(200, "off")
    assert len(visible(store)) == 1
    store.set_memory_mode_override("guild:1", "write_only")
    assert visible(store) == []
    store.set_memory_mode_override("guild:1", None)
    assert len(visible(store)) == 1


@pytest.mark.parametrize("fault", ["implicit", "foreign", "wrong_evidence", "missing"])
def test_untrusted_or_out_of_scope_input_never_creates_an_item(store, fault):
    if fault != "missing":
        source(store, guild=2 if fault == "foreign" else 1, direct=fault != "implicit")
    with pytest.raises(ValueError):
        store.add_server_memory(
            1, content="스스로 만든 주장",
            evidence="없는 인용" if fault == "wrong_evidence" else "매주 모임",
            sources=[ServerSourceRef("99999" if fault == "missing" else "1001", "user")],
        )
    assert store._server_memories.items(1) == []


def test_admin_trust_and_directives_cannot_be_forged_by_extractor(store):
    source(store)
    with pytest.raises(ValueError, match="admins"):
        remember(store, usage_mode="directive")
    item = remember(store, usage_mode="ambient")
    assert store._server_memories.items(1)[0]["id"] == item
    assert store._server_memories.items(1)[0]["usage_mode"] == "contextual"


def test_personal_write_off_and_public_contribution_opt_out(store):
    source(store)
    store.set_user_memory_mode(100, "read_only")
    with pytest.raises(PermissionError):
        remember(store)
    store.set_user_memory_mode(100, "normal")
    item = remember(store)
    assert len(visible(store)) == 1
    assert store.set_server_memory_opt_out(1, 100, True) == 1
    assert store.server_memory_opted_out(1, 100)
    assert visible(store) == []
    assert store._server_memories.sources(item) == []
    with pytest.raises(PermissionError):
        remember(store)
    store.set_server_memory_opt_out(1, 100, False)
    assert not store.server_memory_opted_out(1, 100)
    assert visible(store) == []  # Opt-in does not resurrect deleted data.


def test_private_channel_remains_local_and_cannot_mix_public_and_private(store):
    source(store, public=False, channel=10)
    item = remember(store)
    assert visible(store)[0]["id"] == item
    assert not visible(store, scope=Scope(1, 11, 200, True), channels=(10, 11))
    source(store, message=1002, channel=11)
    with pytest.raises(ValueError, match="disclosure spaces"):
        store.add_server_memory(
            1, content="cross-space", evidence="매주 모임",
            sources=[ServerSourceRef("1001", "user"), ServerSourceRef("1002", "user")],
        )
    assert len(store._server_memories.items(1)) == 1


def test_member_subject_must_speak_for_self_and_be_relevant(store):
    source(store)
    with pytest.raises(ValueError, match="subject"):
        remember(store, subject_user_ids=(999,))
    remember(store, subject_user_ids=(100,))
    assert not visible(store)  # Unrelated member's profile cannot leak.
    assert not visible(store, related=(100,), members=(200,))
    assert len(visible(store, related=(100,))) == 1
    assert not visible(store, scope=Scope(1, 10, 100, True), related=(100,))


def test_two_independent_contributors_upgrade_trust_but_not_claimed_admin(store):
    source(store, user=100, message=1001)
    source(store, user=101, message=1002, content="매주 모임 맞아요")
    item = remember(store, usage_mode="ambient")
    again = remember(store, message=1002, usage_mode="ambient")
    assert item == again
    saved = store._server_memories.items(1)[0]
    assert saved["trust"] == "corroborated"
    assert saved["usage_mode"] == "ambient"
    assert saved["confirmations"] == 2
    assert {x["author_user_id"] for x in store._server_memories.sources(item)} == {"100", "101"}


def test_hina_reply_is_observed_only_for_non_member_community_memory(store):
    source(store)
    item = store.add_server_memory(
        1, content="히나가 다음 주 모임에 인사하기로 함",
        evidence="다음 주에도 만나자",
        sources=[ServerSourceRef("1001", "hina")], usage_mode="ambient",
    )
    assert store._server_memories.items(1)[0]["trust"] == "observed"
    assert store._server_memories.items(1)[0]["id"] == item
    source(store, message=1002, history_lookup=True)
    with pytest.raises(ValueError, match="lookup"):
        store.add_server_memory(
            1, content="재검색 후 생성된 약속", evidence="다음 주에도 만나자",
            sources=[ServerSourceRef("1002", "hina")],
        )


def test_deleting_source_removes_item_and_evidence_atomically(store):
    source(store)
    item = remember(store)
    assert store.forget_server_memory_source(1, 99, 1001) == 0
    assert len(visible(store)) == 1
    assert store.forget_server_memory_source(1, 10, 1001) == 1
    assert store._server_memories.sources(item) == []
    assert visible(store) == []


@pytest.mark.parametrize("action", ["forget", "purge_channel", "purge_realm", "purge_all"])
def test_all_existing_deletion_paths_remove_community_sources(store, action):
    source(store)
    item = remember(store)
    scope = Scope(1, 10, 100, True)
    if action == "forget":
        store.forget(scope)
    elif action == "purge_channel":
        store.purge_channel_memory(scope)
    elif action == "purge_realm":
        store.purge_realm_memory(scope)
    else:
        store.purge_all_memory()
    assert store._server_memories.items(1) == []
    assert store._server_memories.sources(item) == []


def test_live_permission_matrix_does_not_fall_back_to_old_channel_metadata(store):
    source(store, message=1001, channel=10)
    source(store, message=1002, channel=11)
    item = store.add_server_memory(
        1, content="공개 채널 두 곳에서 확인한 공지", evidence="매주 모임",
        sources=[ServerSourceRef("1001", "user"), ServerSourceRef("1002", "user")],
    )
    assert not visible(store, channels=(10,))
    assert visible(store, channels=(10, 11))[0]["id"] == item
    assert store._server_memories.purge_channel(1, 11) == 1
    assert visible(store, channels=(10, 11)) == []


def test_replaying_sources_neither_confirms_nor_resurrects(store):
    source(store)
    remember(store)
    remember(store)
    assert visible(store)[0]["confirmations"] == 1
    assert store.forget_server_memory_source(1, 10, 1001) == 1
    with pytest.raises(ValueError, match="explicitly"):
        remember(store)


@pytest.mark.asyncio
@pytest.mark.parametrize("withdraw", [None, "delete", "optout"])
async def test_multi_author_extraction_rechecks_consent_and_selects_by_tier(store, withdraw):
    from types import SimpleNamespace

    from hina_bot.ai.server_memory_extraction import extract_server_batch
    from hina_bot.core.config import Settings
    from hina_bot.core.server_memory_selection import select_server_rows

    for index in range(16):
        source(store, user=100 + index % 2, message=1001 + index)
    source(store, message=5000, direct=False, content="주변 대화")
    source(store, message=5001, channel=11, public=False, content="비공개")
    async def provider(operation, policy, payload, plan):
        assert operation == "extract_server_memory"
        assert len(payload["turns"]) == 16
        assert all(t["content"] == "이 서버는 매주 모임을 해요" for t in payload["turns"])
        if withdraw == "delete":
            store.forget_server_memory_source(1, 10, 1001)
        elif withdraw == "optout":
            store.set_server_memory_opt_out(1, 100, True)
        return SimpleNamespace(status="completed", output_text='[{"content":"매주 모임",'
            '"evidence":"매주 모임","sources":[{"role":"user","message_id":"1001"},'
            '{"role":"user","message_id":"1002"}],"keywords":["모임"],"usage_mode":"ambient",'
            '"time_scope":"recurring"}]')
    settings = Settings(discord_token="test", model="test", server_memory_read="off")
    llm = SimpleNamespace(settings=settings, _memory_request=provider,
                          usage=SimpleNamespace(routing_event=lambda *args, **kwargs: None))
    await extract_server_batch(llm, store, 1, 10)
    rows = visible(store)
    if withdraw:
        assert not rows
    else:
        assert len(rows) == 1 and rows[0]["trust"] == "corroborated"
        assert select_server_rows(rows, "안녕")[0]["tier"] == "S1"
        assert select_server_rows(rows, "모임")[0]["tier"] == "S2"
        assert select_server_rows(rows, "모임", lookup=True)[0]["tier"] == "S3"
        assert select_server_rows(rows, "전혀 다른 질문") == []


@pytest.mark.asyncio
async def test_server_recall_revalidates_discord_and_toggle_per_call(store):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock, MagicMock

    import discord

    from hina_bot.discord.server_memory import server_reader

    source(store)
    remember(store, keywords=["모임"])
    perms = SimpleNamespace(view_channel=True, read_message_history=True)
    channel = MagicMock(spec=discord.TextChannel)
    channel.permissions_for.return_value = perms
    guild = SimpleNamespace(default_role=object(), fetch_channel=AsyncMock(return_value=channel))
    guild.fetch_member = AsyncMock(side_effect=lambda uid: SimpleNamespace(id=uid, display_name=str(uid)))
    settings = SimpleNamespace(personal_memory_read_mode="legacy", server_memory_read="on")
    client = SimpleNamespace(store=store, settings=settings, events=MagicMock())
    read = server_reader(client, SimpleNamespace(guild=guild), Scope(1, 12, 200, True))
    store.set_user_memory_mode(200, "off")
    assert (await read("모임", lookup=True))[0]["tier"] == "S3"
    perms.view_channel = False
    assert not await read("모임", lookup=True)
    assert guild.fetch_channel.await_count == 2
    settings.server_memory_read = "off"
    perms.view_channel = True
    assert not await read("모임", lookup=True)
    assert guild.fetch_channel.await_count == 2


def test_disputed_memory_is_hidden_pending_operator_review(store):
    source(store)
    original = remember(store)
    source(store, message=1002, content="모임이 취소됐어요")
    replacement = store.add_server_memory(1, content="모임 취소", evidence="취소",
        sources=[ServerSourceRef("1002", "user")], relation="corrects", relation_target=original)
    assert not visible(store)
    with pytest.raises(ValueError):
        store._server_memories.manage_item(2, replacement, "approve")
    store._server_memories.manage_item(1, original, "retract")
    store._server_memories.manage_item(1, replacement, "approve")
    assert [r["id"] for r in visible(store)] == [replacement]


@pytest.mark.asyncio
async def test_server_eval_scores_existing_corroboration_fixture():
    import json
    from types import SimpleNamespace

    from hina_bot.core.config import Settings
    from hina_bot.tooling.memory_extraction_eval import DEFAULT_CASES, load_cases
    from hina_bot.tooling.server_extraction_eval import run_server_case

    case, = load_cases(DEFAULT_CASES, ids=("server_extract_group_event_corroborated",), target="server")

    async def request(operation, instructions, payload, plan):
        rows = payload["turns"]
        return SimpleNamespace(status="completed", output_text=json.dumps([{
            "content": "서버 레이드를 클리어했어요", "evidence": "레이드",
            "time_scope": "episodic", "event_at": "2026-10-29",
            "sources": [{"message_id": rows[0]["message_id"], "role": "user", "evidence": "레이드"},
                        {"message_id": rows[1]["message_id"], "role": "user", "evidence": "세 시간"}],
        }]))

    worker = SimpleNamespace(settings=Settings(discord_token="eval"), _memory_request=request,
                             usage=SimpleNamespace(routing_event=lambda *args, **kwargs: None))
    result = await run_server_case(worker, case)
    assert result["failures"] == []
    assert result["status"] == "passed"

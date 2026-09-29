from hina_bot.dashboard.scopepicker import (
    infer_target_scope_type,
    normalize_scope_filter,
)


def test_scope_filter_normalizes_structured_guild_and_dm_values():
    guild = normalize_scope_filter(
        scope_type="guild",
        guild_id="123",
        channel_id="456",
    )
    assert guild.scope_type == "guild"
    assert guild.guild_id == "123"
    assert guild.channel_id == "456"
    assert guild.realm == "guild:123"
    assert guild.realm_prefix == ""

    all_guilds = normalize_scope_filter(scope_type="guild")
    assert all_guilds.realm == ""
    assert all_guilds.realm_prefix == "guild:"

    dm = normalize_scope_filter(scope_type="dm", channel_id="789")
    assert dm.scope_type == "dm"
    assert dm.guild_id == ""
    assert dm.channel_id == "789"
    assert dm.realm == ""
    assert dm.realm_prefix == "dm:"

    all_dms = normalize_scope_filter(scope_type="dm")
    assert all_dms.realm == ""
    assert all_dms.realm_prefix == "dm:"


def test_scope_filter_preserves_legacy_raw_realm_urls():
    guild = normalize_scope_filter(
        legacy_realm="guild:123",
        channel_id="456",
    )
    assert guild.scope_type == "guild"
    assert guild.guild_id == "123"
    assert guild.realm == "guild:123"
    assert guild.channel_id == "456"

    dm = normalize_scope_filter(legacy_realm="dm:789")
    assert dm.scope_type == "dm"
    assert dm.channel_id == ""
    assert dm.realm == "dm:789"


def test_any_scope_ignores_stale_structured_ids():
    scope = normalize_scope_filter(
        scope_type="any",
        guild_id="123",
        channel_id="456",
    )
    assert scope.scope_type == ""
    assert scope.guild_id == ""
    assert scope.channel_id == ""
    assert scope.realm == ""
    assert scope.realm_prefix == ""

    legacy_channel_only = normalize_scope_filter(channel_id="456")
    assert legacy_channel_only.scope_type == ""
    assert legacy_channel_only.channel_id == "456"


def test_target_scope_type_preserves_legacy_dm_and_defaults_to_guild():
    assert infer_target_scope_type() == "guild"
    assert infer_target_scope_type(guild_id="123") == "guild"
    assert infer_target_scope_type(channel_id="456", user_id="789") == "dm"
    assert infer_target_scope_type(
        scope_type="dm",
        guild_id="123",
        channel_id="456",
        user_id="789",
    ) == "dm"

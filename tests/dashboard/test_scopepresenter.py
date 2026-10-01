import pytest

from hina_bot.dashboard.scopepresenter import parse_scope_key, present_turn_scope


@pytest.mark.parametrize(
    ("raw", "level", "realm", "channel_id", "user_id"),
    [
        ("global", "global", "", "", ""),
        ("guild:123", "realm", "guild:123", "", ""),
        ("dm:789", "realm", "dm:789", "", ""),
        ("guild:123:channel:456", "channel", "guild:123", "456", ""),
        ("dm:789:channel:456", "channel", "dm:789", "456", ""),
        ("guild:123:user:789", "user", "guild:123", "", "789"),
        ("dm:789:user:789", "user", "dm:789", "", "789"),
        ("guild:123:channel:456:user:789", "conversation", "guild:123", "456", "789"),
        ("dm:789:channel:456:user:789", "conversation", "dm:789", "456", "789"),
    ],
)
def test_scope_key_variants(raw, level, realm, channel_id, user_id):
    scope = parse_scope_key(raw)
    assert scope.raw == raw
    assert scope.level == level
    assert scope.realm == realm
    assert scope.channel_id == channel_id
    assert scope.user_id == user_id


def test_dm_realm_owner_and_explicit_user_are_separate_and_ids_are_verbatim():
    scope = parse_scope_key("dm:001:channel:002:user:003")
    assert scope.realm_kind == "dm"
    assert scope.realm_id == "001"
    assert scope.realm == "dm:001"
    assert scope.channel_id == "002"
    assert scope.user_id == "003"
    assert scope.raw == "dm:001:channel:002:user:003"


@pytest.mark.parametrize(
    "raw",
    [
        "", "legacy", " global ", "guild:", "guild:abc", "guild:-1",
        "guild:1:channel:", "guild:1:user:2:channel:3", "dm:1:user:2:user:3",
        "guild:1:channel:2:extra:3", "guild:1\n", "config:chatlog_capture:global",
        "legacy:<script>alert('scope')</script>",
    ],
)
def test_unknown_scope_preserves_raw_without_guessing_components(raw):
    scope = parse_scope_key(raw)
    assert scope.raw == raw
    assert scope.level == "unknown"
    assert scope.realm == scope.realm_id == scope.channel_id == scope.user_id == ""


def test_turn_scope_preserves_explicit_metadata_and_unknown_keys():
    row = {"scope": "dm:001:channel:002:user:003", "realm": "dm:001",
           "user_id": "004", "name": "Stored name"}
    view = present_turn_scope(row)
    assert all(view[key] == value for key, value in row.items())
    assert view["scope_display"].user_id == "003"
    assert view["user_id"] == "004"
    assert view["realm_display"].realm_id == "001"
    assert "scope_display" not in row
    legacy = present_turn_scope({**row, "scope": "legacy:<scope>"})
    assert legacy["scope_display"].level == "unknown"
    assert legacy["scope_display"].raw == "legacy:<scope>"
    assert legacy["realm_display"] == view["realm_display"]
    assert present_turn_scope({"scope": row["scope"]})["realm_display"] == view["realm_display"]

from hina_bot.dashboard.filterutils import applied_filters, remove_filter_url, validate_filters


def test_range_validation():
    assert validate_filters({"after": "2026-10-01T10:00", "before": "2026-10-01T09:00"}, "Asia/Seoul")
    assert not validate_filters({"confidence_min": "0", "confidence_max": "1"}, "Asia/Seoul")


def test_applied_scope_filters_and_compound_removal():
    filters = applied_filters({"filters": {
        "origin_scope_type": "guild", "origin_guild_id": " 1 ",
        "origin_channel_id": "10", "origin_realm": "guild:1", "q": "test",
        "confidence_min": "0.50", "relationship": "ignored",
    }})
    assert "origin_realm" not in filters
    assert "relationship" not in filters
    assert filters["confidence_min"] == "0.5"
    assert remove_filter_url("/memory", filters, "origin_scope_type") == "/memory?q=test&confidence_min=0.5"
    assert "origin_channel_id=10" in remove_filter_url("/memory", filters, "origin_guild_id")
    assert "origin_guild_id=1" in remove_filter_url("/memory", filters, "origin_channel_id")
    assert applied_filters({"filters": filters, "filter_errors": {"after": "invalid"}}) == {}

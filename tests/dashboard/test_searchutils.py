from hina_bot.dashboard.searchutils import search_matches


def test_search_matches_reports_field_and_safe_fragment():
    matches = search_matches(
        "coffee",
        (
            ("input", "I like coffee very much"),
            ("reply", "tea"),
        ),
    )

    assert len(matches) == 1
    assert matches[0]["field"] == "input"
    assert matches[0]["fragment"]["match"] == "coffee"


def test_search_matches_are_case_insensitive():
    matches = search_matches("COFFEE", (("content", "coffee"),))

    assert matches[0]["fragment"]["match"] == "coffee"

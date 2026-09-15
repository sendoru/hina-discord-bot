from hina_bot.core.admin_list import clip_display, display_width, fit_table, sort_rows


def test_display_width_handles_korean_and_ascii():
    assert display_width("abc") == 3
    assert display_width("히나") == 4
    assert display_width("A히나") == 5
    assert display_width(clip_display("히나의 기관총", 8)) <= 8


def test_fit_table_aligns_and_truncates():
    rows = [[f"item-{i}", "사실/ON", "09-12 10:00Z", "히나 관련 설명 " * 4] for i in range(20)]
    text = fit_table(
        "knowledge 20/200 · 추가 시간순",
        ["ID", "종류/상태", "추가(UTC)", "내용"],
        rows,
        [12, 9, 12, 24],
        max_chars=600,
    )
    assert "```text" in text
    assert "ID" in text
    assert "종류/상태" in text
    assert "검색어를 넣어 범위를 줄일 수 있어요" in text
    assert len(text) <= 600


def test_sort_rows_defaults_to_creation_time_and_supports_recent():
    rows = [
        {"id": "new", "enabled": True, "created_at": "2026-09-12T11:00:00+00:00"},
        {"id": "legacy", "enabled": True},
        {"id": "old", "enabled": False, "created_at": "2026-09-12T10:00:00+00:00"},
    ]
    assert [row["id"] for row in sort_rows(rows, "time", lambda row: row)] == [
        "legacy", "old", "new"]
    assert [row["id"] for row in sort_rows(rows, "recent", lambda row: row)] == [
        "new", "old", "legacy"]

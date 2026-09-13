from datetime import UTC, datetime

from hina_bot.ai.freshness import (
    FreshnessMode,
    classify_freshness,
    is_live_domain,
    is_location_dependent,
    needs_location_clarification,
)
from hina_bot.ai.runtime_context import build_runtime_context, runtime_instruction
from hina_bot.core.config import Settings


def test_clock_queries_do_not_need_external_freshness():
    assert classify_freshness("지금 몇 시야?") == FreshnessMode.CLOCK
    assert classify_freshness("오늘 무슨 요일이야?") == FreshnessMode.CLOCK


def test_live_domains_require_fresh_data():
    assert classify_freshness("이번 추석 연휴 시작까지 며칠 남았어?") == FreshnessMode.REQUIRED
    assert classify_freshness("오늘 코스피 얼마야?") == FreshnessMode.REQUIRED
    assert classify_freshness("이번 주 블루아카 업데이트 뭐 있어?") == FreshnessMode.REQUIRED
    assert is_live_domain("현재 대통령이 누구야?") is True


def test_ambiguous_temporal_question_is_auto_not_forced():
    assert classify_freshness("현재 대통령이 누구야?") == FreshnessMode.REQUIRED
    assert classify_freshness("오늘 뭐 먹지?") == FreshnessMode.AUTO
    assert classify_freshness("오늘 좀 피곤하네") == FreshnessMode.STATIC
    assert classify_freshness("지금 뭐해?") == FreshnessMode.STATIC


def test_location_dependency_is_detected_separately():
    assert is_location_dependent("지금 날씨 어때?") is True
    assert is_location_dependent("이번 추석 연휴 언제야?") is False
    assert needs_location_clarification("지금 날씨 어때?") is True
    assert needs_location_clarification("서울 날씨 어때?") is False
    assert needs_location_clarification("2호선 지하철 운행 정상?") is False
    assert needs_location_clarification("내가 있는 곳 날씨 어때?") is True


def test_runtime_context_converts_clock_to_configured_timezone():
    settings = Settings("test", "test", runtime_timezone="Asia/Seoul",
                        runtime_locale="ko-KR", runtime_default_location="서울")
    context = build_runtime_context(
        settings,
        now=datetime(2026, 9, 13, 8, 30, tzinfo=UTC),
    )
    assert context["current_datetime"] == "2026-09-13T17:30:00+09:00"
    assert context["weekday"] == "일요일"
    assert context["daypart"] == "오후"
    assert context["season"] == "가을"
    assert context["day_type"] == "주말"
    assert context["default_location"] == "서울"
    instruction = runtime_instruction(context)
    assert "2026-09-13T17:30:00+09:00" in instruction
    assert "시간대=오후" in instruction
    assert "계절=가을" in instruction
    assert "사용자의 실제 현재 위치라고 주장하지 마세요" in instruction


def test_runtime_instruction_supports_ambient_morning_conversation():
    settings = Settings("test", "test", runtime_timezone="Asia/Seoul", runtime_locale="ko-KR")
    context = build_runtime_context(
        settings,
        now=datetime(2026, 9, 13, 22, 57, tzinfo=UTC),
    )

    assert context["current_datetime"] == "2026-09-14T07:57:00+09:00"
    assert context["weekday"] == "월요일"
    assert context["daypart"] == "아침"
    assert context["season"] == "가을"
    assert context["day_type"] == "평일"

    instruction = runtime_instruction(context)
    assert "사용자의 상태·일정·행동과 관련 있을 때 대화에 자연스럽게" in instruction
    assert "현재 시점과 모순되는 시간대 표현" in instruction
    assert "날씨처럼 제공되지 않은 현재 환경 정보는 추측하지 마세요" in instruction

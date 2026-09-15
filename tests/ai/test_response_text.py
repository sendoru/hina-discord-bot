from types import SimpleNamespace

from hina_bot.ai.web_search_text import response_text, strip_internal_control_prefix


def test_strips_concatenated_internal_control_objects():
    text = (
        '{"calculator":[{"expression":"1+1","prefix":"","suffix":""}],'
        '"response_length":"short"}'
        '{"calculator":[{"expression":"0","prefix":"","suffix":""}],'
        '"response_length":"short"}'
        '마키를 진짜로 혼낼 필요까진 없잖아.'
    )
    assert strip_internal_control_prefix(text) == "마키를 진짜로 혼낼 필요까진 없잖아."


def test_response_text_sanitizes_even_when_citations_are_visible():
    response = SimpleNamespace(
        output_text=(
            '{"calculator":[{"expression":"1+1"}],"response_length":"short"}'
            '그 정도면 장난으로 받아치면 돼.'
        ),
        output=[],
    )
    assert response_text(response, hide_citations=False) == "그 정도면 장난으로 받아치면 돼."


def test_does_not_strip_normal_json_content():
    text = '{"name":"히나","role":"선도부장"}\n이건 일반 JSON이야.'
    assert strip_internal_control_prefix(text) == text

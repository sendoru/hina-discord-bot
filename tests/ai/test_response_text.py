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


def test_hidden_provider_citation_does_not_remove_model_authored_link():
    provider_url = "https://vertexaisearch.cloud.google.com/grounding-api-redirect/token"
    text = "출처는 공식 위키야. (https://example.com/wiki)"
    response = SimpleNamespace(
        output_text=text + " (vertexaisearch.cloud.google.com/grounding-api-redirect/token)",
        output=[
            SimpleNamespace(
                type="message",
                content=[
                    SimpleNamespace(
                        type="output_text",
                        text=text + " (vertexaisearch.cloud.google.com/grounding-api-redirect/token)",
                        annotations=[
                            SimpleNamespace(
                                type="url_citation",
                                start_index=len(text),
                                end_index=len(text) + len(
                                    " (vertexaisearch.cloud.google.com/grounding-api-redirect/token)"
                                ),
                                url=provider_url,
                                title="source",
                            )
                        ],
                    )
                ],
            )
        ],
    )

    assert response_text(response, hide_citations=True) == text


def test_hidden_citations_preserve_code_and_whitespace():
    text = '설명:\n```python\nif True:\n    print("a  !")\n```\n다음  문장'
    response = SimpleNamespace(
        output_text=text,
        output=[SimpleNamespace(
            type="message",
            content=[SimpleNamespace(type="output_text", text=text, annotations=[])],
        )],
    )
    assert response_text(response, hide_citations=True) == text


def test_format_markers_are_not_removed_without_structured_evidence():
    text = r"문자 그대로의 \n, <b>태그</b>, 참고문헌 [1], 배열[1]"
    response = SimpleNamespace(
        output_text=text,
        output=[SimpleNamespace(
            type="message",
            content=[SimpleNamespace(type="output_text", text=text, annotations=[])],
        )],
    )
    assert response_text(response, hide_citations=True) == text
    assert response_text(response, hide_citations=False) == text

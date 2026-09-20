"""Semantic provider-managed capabilities exposed to conversational answers."""

from __future__ import annotations

from enum import StrEnum


class ManagedToolCapability(StrEnum):
    CODE_EXECUTION = "code_execution"


CODE_EXECUTION_POLICY = """[정확한 계산과 코드 실행]
정확한 산술, 수치 비교, 비율·백분율, 방정식처럼 코드로 검증할 수 있는 계산이 현재 답변에
필요하면 제공된 Python/code execution 도구를 사용하세요. 쉬운 계산처럼 보여도 정확성이 중요하면
도구 결과를 우선하세요.

소프트웨어 버전, 날짜, IP 주소, 식별자처럼 점이나 숫자가 구분자로 쓰인 표기는 문맥상 실제
수치 계산을 요구하는 경우가 아니면 임의로 소수로 해석하지 마세요. 도구 실행 과정이나 내부
Python 코드는 사용자가 요청하지 않는 한 설명하지 말고, 결과만 자연스럽게 답변에 반영하세요.
"""


_PROVIDER_BINDINGS = {
    "gemini": {
        ManagedToolCapability.CODE_EXECUTION: {"type": "code_execution"},
    },
    "openai": {
        ManagedToolCapability.CODE_EXECUTION: {
            "type": "code_interpreter",
            "container": {"type": "auto"},
        },
    },
}


def managed_tool_config(
    provider: str,
    capabilities: tuple[ManagedToolCapability, ...] = (
        ManagedToolCapability.CODE_EXECUTION,
    ),
) -> list[dict]:
    """Map semantic managed-tool capabilities to provider-native tool declarations."""

    bindings = _PROVIDER_BINDINGS.get(provider, {})
    return [
        dict(bindings[capability])
        for capability in capabilities
        if capability in bindings
    ]


def search_tool_choice(provider: str, search_mode: str, *, has_managed_tools: bool):
    """Preserve required-web semantics when other provider-managed tools coexist."""

    if search_mode == "required":
        if provider == "openai":
            return {"type": "web_search"}
        return "required"
    if has_managed_tools:
        return "auto"
    return None


__all__ = [
    "CODE_EXECUTION_POLICY",
    "ManagedToolCapability",
    "managed_tool_config",
    "search_tool_choice",
]

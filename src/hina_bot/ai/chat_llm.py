"""Current chat LLM with information-source routing layered over the v2 implementation."""

from .chat_llm_v2 import LLM as BaseLLM
from .information_evidence import search_mode
from .information_routing import (
    classify_information_request,
    looks_like_relation_or_event_question,
    looks_like_world_fact_question,
)


class LLM(BaseLLM):
    @staticmethod
    def _looks_like_relation_or_event_question(content: str) -> bool:
        return looks_like_relation_or_event_question(content)

    @classmethod
    def _looks_like_world_fact_question(cls, content: str) -> bool:
        return looks_like_world_fact_question(content)

    def lore_references(self, content: str) -> list[dict]:
        request = classify_information_request(content)
        return super().lore_references(request.lore_query)

    def _web_search_mode(self, content, references, freshness=None) -> str:
        request = classify_information_request(content, freshness=freshness)
        return search_mode(
            request,
            references,
            enabled=self.settings.chat_web_search,
            default_location=getattr(self.settings, "runtime_default_location", ""),
        )


__all__ = ["LLM"]

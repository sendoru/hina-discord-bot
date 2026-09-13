"""Current chat LLM with information-source routing layered over the v2 implementation."""

from .chat_llm_v2 import LLM as BaseLLM
from .information_evidence import search_mode
from .information_routing import (
    classify_information_request,
    looks_like_relation_or_event_question,
    looks_like_world_fact_question,
)
from .self_profile_lore import fallback_references


class LLM(BaseLLM):
    @staticmethod
    def _looks_like_relation_or_event_question(content: str) -> bool:
        return looks_like_relation_or_event_question(content)

    @classmethod
    def _looks_like_world_fact_question(cls, content: str) -> bool:
        return looks_like_world_fact_question(content)

    def lore_references(self, content: str) -> list[dict]:
        request = classify_information_request(content)
        references = super().lore_references(request.lore_query)
        if not request.self_profile:
            return references

        existing = {str(row.get("reference", "")) for row in references}
        fallbacks = [
            row for row in fallback_references(request.lore_query)
            if str(row.get("reference", "")) not in existing
        ]
        return fallbacks + references

    def _web_search_mode(self, content, references, freshness=None) -> str:
        request = classify_information_request(content, freshness=freshness)
        mode = search_mode(
            request,
            references,
            enabled=self.settings.chat_web_search,
            default_location=getattr(self.settings, "runtime_default_location", ""),
        )
        if request.relation_or_event and mode == "none":
            trusted = any(
                str(row.get("reference", "")).startswith(("canon.", "runtime_lore."))
                for row in references
                if row.get("kind") == "world_fact"
            )
            if not trusted:
                return "required" if self.settings.chat_web_search else "none"
        return mode


__all__ = ["LLM"]

"""Current chat LLM with information-source routing layered over the v2 implementation."""

import re

from .chat_llm_v2 import LLM as BaseLLM
from .freshness import FreshnessMode
from .information_evidence import search_mode
from .information_routing import (
    classify_information_request,
    looks_like_relation_or_event_question,
    looks_like_world_fact_question,
)
from .self_profile_lore import fallback_references

_IN_WORLD_PRESENT_STATE_QUERY = re.compile(
    r"(?:지금|현재|오늘|요즘).*(?:무슨\s*일|사고|소동|난리|사건|전투|공격|습격|폭발|"
    r"불(?:이|났|나)|터졌|발생|일어났|문제\s*(?:생|있)|상황|괜찮|평온|시끄럽|조용)|"
    r"(?:무슨\s*일|사고|소동|난리|사건|전투|공격|습격|폭발|불(?:이|났|나)|터졌|발생|"
    r"일어났|문제\s*(?:생|있)|상황|괜찮|평온|시끄럽|조용).*(?:지금|현재|오늘|요즘)",
    re.IGNORECASE,
)
_EXTERNAL_PRESENT_STATE_MARKER = re.compile(
    r"(?:공식|뉴스|기사|방송|SNS|트위터|X\b|유튜브|굿즈|상품|판매|출시|업데이트|패치|"
    r"서버|한섭|한국\s*서버|일섭|재고|예약)",
    re.IGNORECASE,
)


class LLM(BaseLLM):
    @staticmethod
    def _looks_like_relation_or_event_question(content: str) -> bool:
        return looks_like_relation_or_event_question(content)

    @classmethod
    def _looks_like_world_fact_question(cls, content: str) -> bool:
        return looks_like_world_fact_question(content)

    @staticmethod
    def _looks_like_in_world_present_state(
        content: str,
        references: list[dict],
        freshness: FreshnessMode,
    ) -> bool:
        """Treat fictional 'right now' roleplay as local context, not live-world search.

        Temporal words such as '지금' are intentionally broad in the freshness classifier. When
        the same message is anchored by retrieved world lore and asks about an in-world incident
        or state, offering a live web-search tool is usually a category error. Explicit external
        markers keep release/news/merchandise questions on the normal live-information path.
        """
        if freshness != FreshnessMode.AUTO:
            return False
        if _EXTERNAL_PRESENT_STATE_MARKER.search(content):
            return False
        if not _IN_WORLD_PRESENT_STATE_QUERY.search(content):
            return False
        return any(row.get("kind") == "world_fact" for row in references)

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
        if self._looks_like_in_world_present_state(
            content,
            references,
            request.freshness,
        ):
            return "none"
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

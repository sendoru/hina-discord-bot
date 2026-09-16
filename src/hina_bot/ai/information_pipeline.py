"""Classify information needs, retrieve lore, and decide evidence/search routing."""

import asyncio
import logging
import re

from .ambient_weather import CURRENT_AMBIENT_WEATHER, AmbientWeatherCache
from .freshness import FreshnessMode, is_live_domain
from .information_evidence import search_mode
from .information_plan import InformationPlan
from .information_routing import (
    InformationRoute,
    classify_information_request,
    looks_like_relation_or_event_question,
    looks_like_world_fact_question,
)
from .memory_summary import MemorySummaryMixin
from .model_routing import build_model_plan
from .note_context import NoteContextStore
from .request_assembly import RequestAssembler
from .routing_plan import RoutingPlan
from .rp_output_policy import provenance_mode
from .semantic_model_routing import SemanticModelRouter
from .vision import CURRENT_VISUAL_INPUTS

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
log = logging.getLogger("hina")


class InformationPipeline(MemorySummaryMixin, RequestAssembler):
    """Resolve information/evidence decisions before final request assembly."""

    def __init__(self, settings, client=None, classifier_client=None):
        super().__init__(settings, client=client)
        self.ambient_weather = AmbientWeatherCache()
        self._routing_shadow_tasks: set[asyncio.Task] = set()
        self.routing_classifier_client = classifier_client
        if settings.routing_classifier_mode != "off":
            if self.routing_classifier_client is None:
                from .providers import create_provider_client

                self.routing_classifier_client = create_provider_client(
                    settings,
                    settings.routing_classifier_provider,
                    credential=settings.routing_classifier_key(),
                    timeout=settings.routing_classifier_timeout_seconds,
                    max_retries=0,
                    thinking_level="minimal",
                )
            self.semantic_model_router = SemanticModelRouter(
                settings,
                self.routing_classifier_client,
                self.usage,
            )
        else:
            self.semantic_model_router = None

    async def close(self):
        if self._routing_shadow_tasks:
            await asyncio.gather(*tuple(self._routing_shadow_tasks), return_exceptions=True)
        try:
            if self.routing_classifier_client is not None:
                await self.routing_classifier_client.close()
        finally:
            await super().close()

    def _start_shadow_classification(self, information, baseline) -> None:
        task = asyncio.create_task(
            self.semantic_model_router.observe_shadow(information, baseline)
        )
        self._routing_shadow_tasks.add(task)
        task.add_done_callback(self._finish_shadow_classification)

    def _finish_shadow_classification(self, task: asyncio.Task) -> None:
        self._routing_shadow_tasks.discard(task)
        try:
            task.result()
        except asyncio.CancelledError:
            return
        except Exception as exc:  # noqa: BLE001
            log.warning("Shadow model routing failed (%s)", type(exc).__name__)

    def _call_prefixes(self) -> tuple[str, ...] | None:
        return getattr(self.settings, "call_prefixes", None)

    @staticmethod
    def _looks_like_relation_or_event_question(content: str) -> bool:
        return looks_like_relation_or_event_question(content)

    def _looks_like_world_fact_question(self, content: str) -> bool:
        return looks_like_world_fact_question(content, call_prefixes=self._call_prefixes())

    @staticmethod
    def _looks_like_in_world_present_state(
        content: str,
        references: list[dict],
        freshness: FreshnessMode,
    ) -> bool:
        if freshness != FreshnessMode.AUTO:
            return False
        if _EXTERNAL_PRESENT_STATE_MARKER.search(content):
            return False
        if not _IN_WORLD_PRESENT_STATE_QUERY.search(content):
            return False
        return any(row.get("kind") == "world_fact" for row in references)

    def lore_references(self, content: str) -> list[dict]:
        request = classify_information_request(content, call_prefixes=self._call_prefixes())
        return super().lore_references(request.lore_query)

    def _web_search_mode(self, content, references, freshness=None) -> str:
        request = classify_information_request(
            content,
            freshness=freshness,
            call_prefixes=self._call_prefixes(),
        )
        if self._looks_like_in_world_present_state(content, references, request.freshness):
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

    def build_information_plan(self, routing: RoutingPlan) -> InformationPlan:
        """Resolve retrieval/search decisions once, before request assembly begins."""
        query = routing.routing_query
        request = classify_information_request(query, call_prefixes=self._call_prefixes())
        references = self.lore_references(query)
        freshness = request.freshness
        fact_question = self._looks_like_world_fact_question(query) and not (
            freshness == FreshnessMode.REQUIRED and is_live_domain(query)
        )
        web_mode = self._web_search_mode(query, references, freshness)
        return InformationPlan(
            routing=routing,
            route=request.route,
            references=tuple(references),
            freshness=freshness,
            fact_question=fact_question,
            search_mode=web_mode,
            provenance=provenance_mode(query, web_search=web_mode == "required"),
        )

    async def answer(
        self,
        store,
        scope,
        name: str,
        content: str,
        public_context: list | None = None,
        channel_context: list | None = None,
        emoji_catalog: list | None = None,
        use_memory: bool = True,
        routing_plan: RoutingPlan | None = None,
    ) -> str:
        routing = routing_plan or RoutingPlan(content, content)
        information = self.build_information_plan(routing)
        model_plan = build_model_plan(
            self.settings,
            information,
            channel_context=channel_context or (),
            visual_inputs=CURRENT_VISUAL_INPUTS.get(),
        )
        if self.semantic_model_router is not None and self.settings.model_routing_mode == "adaptive":
            if self.settings.routing_classifier_mode == "active":
                model_plan = await self.semantic_model_router.active_plan(
                    information, model_plan
                )
            elif self.settings.routing_classifier_mode == "shadow":
                self._start_shadow_classification(information, model_plan)
        weather = None
        if information.route == InformationRoute.GENERAL:
            weather = await self.ambient_weather.current(self.settings)
        token = CURRENT_AMBIENT_WEATHER.set(weather)
        try:
            assembly_store = store
            assembly_public_context = public_context
            assembly_use_memory = use_memory
            memory_mode = store.memory_mode(scope) if hasattr(store, "memory_mode") else "normal"
            if not use_memory and memory_mode in {"off", "write_only"}:
                assembly_store = NoteContextStore(store)
                assembly_public_context = []
                assembly_use_memory = True
            return await super().answer(
                assembly_store,
                scope,
                name,
                content,
                public_context=assembly_public_context,
                channel_context=channel_context,
                emoji_catalog=emoji_catalog,
                use_memory=assembly_use_memory,
                information_plan=information,
                model_plan=model_plan,
            )
        finally:
            CURRENT_AMBIENT_WEATHER.reset(token)


LLM = InformationPipeline

__all__ = ["LLM", "InformationPipeline"]

"""Classify information needs, retrieve lore, and decide evidence/search routing."""

import asyncio
import logging
import re
from dataclasses import replace

from hina_bot.core.memory_context import CURRENT_MEMORY_CONTEXT, build_memory_context

from .ambient_weather import CURRENT_AMBIENT_WEATHER, AmbientWeatherCache
from .egress_policy import apply_context_policy
from .freshness import FreshnessMode, is_live_domain
from .identity_resolution import SpeakerIdentityCandidate, SpeakerIdentityResolver
from .information_evidence import SearchDecision, search_decision
from .information_plan import InformationPlan
from .information_routing import (
    InformationRoute,
    classify_information_request,
    looks_like_relation_or_event_question,
    looks_like_world_fact_question,
)
from .memory_summary import MemorySummaryMixin
from .model_routing import baseline_route_state, build_model_plan
from .note_context import NoteContextStore
from .reference_gated_recall import plan_reference_gated_recall
from .request_assembly import RequestAssembler, _context_timestamp
from .routing_plan import RoutingPlan
from .rp_output_policy import provenance_mode
from .semantic_model_routing import (
    SemanticModelRouter,
    apply_web_classification,
    semantic_result,
)
from .structured_memory_context import structured_memory_context
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


def _text_size(value) -> int:
    if isinstance(value, str):
        return len(value)
    if isinstance(value, dict):
        return sum(_text_size(item) for item in value.values())
    if isinstance(value, (list, tuple)):
        return sum(_text_size(item) for item in value)
    return 0


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
        self.speaker_identity_resolver = SpeakerIdentityResolver(
            settings,
            self.client,
            self.usage,
        )

    async def close(self):
        if self._routing_shadow_tasks:
            await asyncio.gather(*tuple(self._routing_shadow_tasks), return_exceptions=True)
        try:
            if self.routing_classifier_client is not None:
                await self.routing_classifier_client.close()
        finally:
            await super().close()

    def _start_shadow_classification(
        self,
        information,
        baseline,
        *,
        context_chars,
        visual_inputs,
    ) -> None:
        task = asyncio.create_task(
            self.semantic_model_router.observe_shadow(
                information,
                baseline,
                context_chars=context_chars,
                visual_inputs=visual_inputs,
            )
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

    async def resolve_speaker_identity(self, request: str, candidates: list[dict]):
        normalized = [
            SpeakerIdentityCandidate(
                user_id=str(candidate.get("user_id") or ""),
                names=tuple(
                    str(name)
                    for name in candidate.get("names", ())
                    if str(name).strip()
                ),
            )
            for candidate in candidates
            if str(candidate.get("user_id") or "")
        ]
        return await self.speaker_identity_resolver.resolve(request, normalized)

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

    def _web_search_decision(
        self,
        content,
        references,
        freshness=None,
    ) -> SearchDecision:
        request = classify_information_request(
            content,
            freshness=freshness,
            call_prefixes=self._call_prefixes(),
        )
        if self._looks_like_in_world_present_state(content, references, request.freshness):
            return SearchDecision("none", True, "in_world_present_state")
        return search_decision(
            request,
            references,
            enabled=self.settings.chat_web_search,
            default_location=getattr(self.settings, "runtime_default_location", ""),
        )

    def _web_search_mode(self, content, references, freshness=None) -> str:
        """Compatibility helper for focused routing tests."""
        return self._web_search_decision(content, references, freshness).mode

    @staticmethod
    def _with_search_provenance(information: InformationPlan) -> InformationPlan:
        return replace(
            information,
            provenance=provenance_mode(
                information.routing.routing_query,
                web_search=information.search_mode == "required",
            ),
        )

    def build_information_plan(self, routing: RoutingPlan) -> InformationPlan:
        query = routing.routing_query
        request = classify_information_request(query, call_prefixes=self._call_prefixes())
        references = self.lore_references(query)
        freshness = request.freshness
        fact_question = self._looks_like_world_fact_question(query) and not (
            freshness == FreshnessMode.REQUIRED and is_live_domain(query)
        )
        web = self._web_search_decision(query, references, freshness)
        return InformationPlan(
            routing=routing,
            route=request.route,
            references=tuple(references),
            freshness=freshness,
            fact_question=fact_question,
            search_mode=web.mode,
            provenance=provenance_mode(query, web_search=web.mode == "required"),
            search_baseline_mode=web.mode,
            search_locked=web.locked,
            search_reason=web.reason,
        )

    def _routing_context_chars(
        self,
        store,
        scope,
        routing_content: str,
        *,
        public_context,
        channel_context,
        use_memory: bool,
        factual_recall_plan=None,
    ) -> int:
        """Measure the dynamic text admitted by the same memory/context policies as assembly."""
        summary, _ = store.summary(scope) if use_memory else ("", 0)
        channel_rows = self._bind_current_speaker(channel_context or [], scope.user_id)
        current_channel_only = self._current_channel_scope_only(scope, routing_content)

        history = []
        if use_memory and scope.guild_id is None:
            used = 0
            turns = []
            for turn in reversed(store.history(scope)):
                size = len(turn["content"]) + len(turn["reply"])
                if used + size > self.settings.history_max_chars:
                    break
                turns.append(turn)
                used += size
            for turn in reversed(turns):
                at = _context_timestamp(turn["created_at"])
                history.extend((
                    {"role": "user", "at": at, "content": turn["content"]},
                    {"role": "assistant", "at": at, "content": turn["reply"]},
                ))

        server_recent = (
            self._server_recent_conversation(store, scope, channel_rows)
            if use_memory
            else []
        )
        cross_channel_memory = use_memory and not current_channel_only
        structured_memory = structured_memory_context(
            store,
            scope,
            use_memory=use_memory,
            allow_cross_space=cross_channel_memory,
            authorized_factual_items=(
                factual_recall_plan.selected
                if factual_recall_plan is not None
                else ()
            ),
        )
        context = {
            "server_note": (
                store.note(scope.realm)
                if cross_channel_memory and scope.guild_id is not None
                else ""
            ),
            "user_note": store.note(scope.user_note) if cross_channel_memory else "",
            "conversation_memory": summary,
            **structured_memory,
            "personal_recent_conversation": server_recent,
            "public_server_context": (
                self.authorized_context(scope, public_context or [])
                if cross_channel_memory
                else []
            ),
            "channel_recent_messages": channel_rows,
            "conversation_history": history,
        }
        context = apply_context_policy(
            context,
            scope.user_id,
            self.settings.external_context_policy,
        )
        return _text_size(context)

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

        assembly_store = store
        assembly_public_context = public_context
        assembly_use_memory = use_memory
        memory_mode = store.memory_mode(scope) if hasattr(store, "memory_mode") else "normal"
        if not use_memory and memory_mode in {"off", "write_only"}:
            assembly_store = NoteContextStore(store)
            assembly_public_context = []
            assembly_use_memory = True

        channel_rows = channel_context or ()
        CURRENT_MEMORY_CONTEXT.set(tuple(build_memory_context(channel_rows, scope.user_id)))
        current_channel_only = self._current_channel_scope_only(
            scope,
            routing.routing_query,
        )
        factual_recall_plan = plan_reference_gated_recall(
            assembly_store,
            scope,
            routing.visible_content,
            use_memory=use_memory,
            allow_cross_space=use_memory and not current_channel_only,
        )
        visual_inputs = CURRENT_VISUAL_INPUTS.get()
        context_chars = self._routing_context_chars(
            assembly_store,
            scope,
            routing.routing_query,
            public_context=assembly_public_context,
            channel_context=channel_rows,
            use_memory=assembly_use_memory,
            factual_recall_plan=factual_recall_plan,
        )
        baseline_score, baseline_tier, local_level = baseline_route_state(
            self.settings,
            information,
            context_chars=context_chars,
            visual_inputs=visual_inputs,
        )

        model_plan = None
        if self.semantic_model_router is not None and self.settings.model_routing_mode == "adaptive":
            if self.settings.routing_classifier_mode == "active":
                reasoning_open = baseline_score < self.settings.model_routing_smart_threshold
                classifier_needed = reasoning_open or not information.search_locked
                level = ""
                codes = ()
                status = "skipped_baseline_smart"
                source = "rule_shortcut" if local_level else "objective"
                if classifier_needed:
                    outcome = await self.semantic_model_router.classify(
                        information,
                        baseline_tier=baseline_tier,
                    )
                    information = self._with_search_provenance(
                        apply_web_classification(information, outcome)
                    )
                    if reasoning_open:
                        level, codes, status = semantic_result(outcome)
                        source = "semantic" if status == "completed" else "rules_fallback"
                model_plan = build_model_plan(
                    self.settings,
                    information,
                    context_chars=context_chars,
                    visual_inputs=visual_inputs,
                    semantic_level=level,
                    semantic_codes=codes,
                    semantic_route_mode="active",
                    semantic_route_status=status,
                    model_route_decision_source=source,
                    model_route_baseline_tier=baseline_tier,
                )
            elif self.settings.routing_classifier_mode == "shadow":
                model_plan = build_model_plan(
                    self.settings,
                    information,
                    context_chars=context_chars,
                    visual_inputs=visual_inputs,
                )
                self._start_shadow_classification(
                    information,
                    model_plan,
                    context_chars=context_chars,
                    visual_inputs=visual_inputs,
                )

        if model_plan is None:
            model_plan = build_model_plan(
                self.settings,
                information,
                context_chars=context_chars,
                visual_inputs=visual_inputs,
            )

        weather = None
        if information.route == InformationRoute.GENERAL:
            weather = await self.ambient_weather.current(self.settings)
        token = CURRENT_AMBIENT_WEATHER.set(weather)
        try:
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
                factual_recall_plan=factual_recall_plan,
            )
        finally:
            CURRENT_AMBIENT_WEATHER.reset(token)


LLM = InformationPipeline

__all__ = ["LLM", "InformationPipeline"]

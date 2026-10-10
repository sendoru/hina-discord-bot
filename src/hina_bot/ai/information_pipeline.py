"""Classify information needs, retrieve lore, and decide evidence/search routing."""

import asyncio
import logging
import re
from dataclasses import replace

from hina_bot.core.evidence_sufficiency import (
    assess_local_evidence,
    selected_evidence_bundle,
)
from hina_bot.core.memory_context import CURRENT_MEMORY_CONTEXT, build_memory_context
from hina_bot.core.retrieval_v2_runtime import comparison_metrics

from .ambient_weather import CURRENT_AMBIENT_WEATHER, AmbientWeatherCache
from .egress_policy import apply_context_policy
from .freshness import FreshnessMode, is_live_domain
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
from .request_assembly import RequestAssembler
from .retrieval_request import build_resolved_retrieval_request
from .retrieval_v2_rollout import RetrievalV2Controller, build_ambient_scene
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

    def __init__(self, settings, client, classifier_client=None):
        super().__init__(settings, client=client)
        self.ambient_weather = AmbientWeatherCache()
        self._routing_shadow_tasks: set[asyncio.Task] = set()
        self._retrieval_shadow_tasks: set[asyncio.Task] = set()
        self.retrieval_v2 = RetrievalV2Controller(settings, self.lore)
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

    async def start_background_tasks(self):
        self.ambient_weather.start_polling(self.settings)

    async def close(self):
        if self._routing_shadow_tasks:
            await asyncio.gather(*tuple(self._routing_shadow_tasks), return_exceptions=True)
        if self._retrieval_shadow_tasks:
            await asyncio.gather(*tuple(self._retrieval_shadow_tasks), return_exceptions=True)
        await self.ambient_weather.close()
        try:
            if self.routing_classifier_client is not None:
                await self.routing_classifier_client.close()
            await self.retrieval_v2.close()
        finally:
            await super().close()

    def _retrieval_v2_candidates(self):
        candidates = []
        for registry in (self.runtime_lore, self.story_context):
            try:
                candidates.extend(registry.candidates())
            except ValueError as exc:
                log.warning(
                    "Retrieval v2 runtime candidates ignored: %s",
                    type(exc).__name__,
                )
        return tuple(candidates)

    async def _run_retrieval_v2(
        self,
        routing,
        *,
        store,
        scope,
        channel_rows,
        use_memory,
    ):
        request = build_resolved_retrieval_request(
            routing,
            call_prefixes=self._call_prefixes(),
        )
        scene = (
            build_ambient_scene(
                store,
                scope,
                channel_rows,
                use_memory=use_memory,
            )
            if request.intent.value == "conversation"
            else None
        )
        return await self.retrieval_v2.retrieve(
            request,
            runtime_candidates=self._retrieval_v2_candidates(),
            scene=scene,
        )

    def _emit_retrieval_v2(
        self,
        mode,
        status,
        legacy_references,
        run=None,
        *,
        selected_context="legacy",
        fallback_reason="",
        **extra,
    ):
        fields = {
            "status": status,
            "retrieval_v2_mode": mode,
            "retrieval_v2_applied": selected_context == "v2",
            "retrieval_v2_selected_context": selected_context,
            "retrieval_v2_semantic_ready": bool(
                getattr(self.retrieval_v2, "semantic_ready", False)
            ),
            "retrieval_v2_semantic_gate": str(
                getattr(self.retrieval_v2, "semantic_gate_reason", "")
            ),
            "retrieval_v2_fallback_reason": fallback_reason,
            **extra,
        }
        if run is not None and run.result is not None:
            fields.update(comparison_metrics(legacy_references, run.result))
        self.usage.routing_event("retrieval.v2", **fields)

    async def _observe_retrieval_v2(
        self,
        routing,
        legacy_references,
        *,
        store,
        scope,
        channel_rows,
        use_memory,
    ):
        try:
            run = await self._run_retrieval_v2(
                routing,
                store=store,
                scope=scope,
                channel_rows=channel_rows,
                use_memory=use_memory,
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - shadow must never affect the answer path
            self._emit_retrieval_v2(
                "shadow",
                "failed",
                legacy_references,
                selected_context="legacy",
                fallback_reason="preflight_error",
                retrieval_v2_error_type=type(exc).__name__,
            )
            return
        self._emit_retrieval_v2(
            "shadow",
            run.status,
            legacy_references,
            run,
            selected_context="legacy",
            fallback_reason=run.fallback_reason,
        )

    def _start_retrieval_v2_shadow(
        self,
        routing,
        legacy_references,
        *,
        store,
        scope,
        channel_rows,
        use_memory,
    ):
        if len(self._retrieval_shadow_tasks) >= 2:
            self._emit_retrieval_v2(
                "shadow",
                "skipped_backpressure",
                legacy_references,
                selected_context="legacy",
                fallback_reason="backpressure",
            )
            return
        task = asyncio.create_task(self._observe_retrieval_v2(
            routing,
            legacy_references,
            store=store,
            scope=scope,
            channel_rows=tuple(channel_rows or ()),
            use_memory=use_memory,
        ))
        self._retrieval_shadow_tasks.add(task)
        task.add_done_callback(self._finish_retrieval_v2_shadow)

    def _finish_retrieval_v2_shadow(self, task):
        self._retrieval_shadow_tasks.discard(task)
        try:
            task.result()
        except asyncio.CancelledError:
            return
        except Exception as exc:  # noqa: BLE001  # pragma: no cover - callback isolation
            log.warning("Retrieval v2 shadow task failed (%s)", type(exc).__name__)

    def _activate_retrieval_v2(self, information, result):
        references = tuple(
            {
                **row.candidate.reference_item(),
                "retrieval_usage": usage,
            }
            for usage, rows in (
                ("relation", result.bundle.relations),
                ("factual", result.bundle.facts),
            )
            for row in rows
        )
        search_mode = information.search_mode
        search_reason = information.search_reason
        search_locked = information.search_locked
        if (
            information.route == InformationRoute.LOCAL_THEN_WEB
            and self.settings.chat_web_search
            and information.search_reason != "in_world_present_state"
        ):
            search_mode = "none" if result.evidence.sufficient else "required"
            search_reason = result.evidence.reason
            search_locked = True
        return replace(
            information,
            references=references,
            search_mode=search_mode,
            provenance=provenance_mode(
                information.routing.routing_query,
                web_search=search_mode == "required",
            ),
            search_locked=search_locked,
            search_reason=search_reason,
            search_decision_source="retrieval_v2",
            retrieval_v2_applied=True,
            retrieval_v2_bundle=result.bundle,
        )

    def _start_shadow_classification(
        self,
        information,
        baseline,
        *,
        context_chars,
        ambient_context_chars,
        visual_inputs,
    ) -> None:
        task = asyncio.create_task(
            self.semantic_model_router.observe_shadow(
                information,
                baseline,
                context_chars=context_chars,
                ambient_context_chars=ambient_context_chars,
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

    def _selected_local_evidence_candidates(self, references):
        identifiers = [
            row.get("reference")
            for row in references
            if isinstance(row.get("reference"), str)
        ]
        if not identifiers:
            return (), ()

        candidates = []
        try:
            for name in ("runtime_lore", "story_context"):
                registry = getattr(self, name, None)
                reader = getattr(registry, "candidates", None)
                if callable(reader):
                    candidates.extend(reader())
            lore = getattr(self, "lore", None)
            reader = getattr(lore, "candidates", None)
            if callable(reader):
                candidates.extend(reader(
                    include_community=getattr(self.settings, "community_lore", True),
                ))
        except ValueError as exc:
            log.warning("Structured local evidence ignored: %s", type(exc).__name__)
            return (), ()

        by_reference = {
            candidate.reference or candidate.candidate_id: candidate
            for candidate in candidates
        }
        selected = tuple(
            by_reference[identifier]
            for identifier in identifiers
            if identifier in by_reference
        )
        return selected, tuple(candidates)

    def _local_evidence_assessment(
        self,
        content,
        references,
        *,
        routing: RoutingPlan | None = None,
    ):
        selected, supporting = self._selected_local_evidence_candidates(references)
        request = build_resolved_retrieval_request(
            routing or RoutingPlan(content, content),
            call_prefixes=self._call_prefixes(),
        )
        return assess_local_evidence(
            request,
            selected_evidence_bundle(selected),
            supporting_candidates=supporting,
        )

    def _web_search_decision(
        self,
        content,
        references,
        freshness=None,
        *,
        routing: RoutingPlan | None = None,
    ) -> SearchDecision:
        request = classify_information_request(
            content,
            freshness=freshness,
            call_prefixes=self._call_prefixes(),
        )
        if self._looks_like_in_world_present_state(content, references, request.freshness):
            return SearchDecision("none", True, "in_world_present_state")
        local_evidence = (
            self._local_evidence_assessment(content, references, routing=routing)
            if request.route == InformationRoute.LOCAL_THEN_WEB
            else None
        )
        return search_decision(
            request,
            references,
            enabled=self.settings.chat_web_search,
            default_location=getattr(self.settings, "runtime_default_location", ""),
            local_evidence=local_evidence,
        )

    @staticmethod
    def _with_search_provenance(information: InformationPlan) -> InformationPlan:
        return replace(
            information,
            provenance=provenance_mode(
                information.routing.routing_query,
                web_search=information.search_mode == "required",
            ),
        )

    def build_information_plan(
        self, routing: RoutingPlan, *, use_lore: bool = True
    ) -> InformationPlan:
        query = routing.routing_query
        request = classify_information_request(query, call_prefixes=self._call_prefixes())
        # In off/v2 mode do not execute the legacy lore or runtime-knowledge
        # retrieval pipeline. Web/freshness routing remains independent of RAG.
        references = self.lore_references(query) if use_lore else []
        freshness = request.freshness
        fact_question = self._looks_like_world_fact_question(query) and not (
            freshness == FreshnessMode.REQUIRED and is_live_domain(query)
        )
        web = self._web_search_decision(query, references, freshness, routing=routing)
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
    ) -> tuple[int, int]:
        """Measure strong and ambient dynamic text admitted by the answer egress policy."""
        channel_rows = self._bind_current_speaker(channel_context or [], scope.user_id)
        current_channel_only = self._current_channel_scope_only(scope, routing_content)

        history, _history_message_ids = (
            self._dm_conversation_history(
                store,
                scope,
                self.settings.history_max_chars,
            )
            if use_memory
            else ([], [])
        )
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
            allow_cross_space=use_memory,
            authorized_factual_items=(
                factual_recall_plan.selected
                if factual_recall_plan is not None
                else ()
            ),
        )
        context = {
            "server_note": (
                store.note(scope.realm)
                if use_memory and scope.guild_id is not None
                else ""
            ),
            "user_note": store.note(scope.user_note) if use_memory else "",
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
        channel_rows = list(context.get("channel_recent_messages", ()) or ())
        ambient_rows = [
            row for row in channel_rows
            if row.get("context_kind") == "channel_ambient"
        ]
        context["channel_recent_messages"] = [
            row for row in channel_rows
            if row.get("context_kind") != "channel_ambient"
        ]
        return _text_size(context), _text_size(ambient_rows)

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
        legacy_mode = getattr(self.settings, "retrieval_v2_mode", "off")
        rag_mode = getattr(self.settings, "rag_mode", None)
        if rag_mode is None:
            rag_mode = {"off": "v1", "shadow": "shadow",
                        "active": "v2"}.get(legacy_mode, "v1")
        information = self.build_information_plan(
            routing, use_lore=rag_mode in {"v1", "shadow"}
        )
        selected_rag = "v1" if rag_mode in {"v1", "shadow"} else "none"

        assembly_store = store
        assembly_public_context = public_context
        assembly_use_memory = use_memory
        memory_mode = store.memory_mode(scope) if hasattr(store, "memory_mode") else "normal"
        if not use_memory and memory_mode in {"off", "write_only"}:
            assembly_store = NoteContextStore(store)
            assembly_public_context = []
            assembly_use_memory = True

        channel_rows = channel_context or ()
        if rag_mode == "shadow":
            self._start_retrieval_v2_shadow(
                routing,
                information.references,
                store=assembly_store,
                scope=scope,
                channel_rows=channel_rows,
                use_memory=assembly_use_memory,
            )
        elif rag_mode == "v2":
            if not self.retrieval_v2.active_ready:
                self._emit_retrieval_v2(
                    "v2",
                    "gated",
                    information.references,
                    selected_context="none",
                    fallback_reason=self.retrieval_v2.semantic_gate_reason,
                )
            else:
                try:
                    run = await self._run_retrieval_v2(
                        routing,
                        store=assembly_store,
                        scope=scope,
                        channel_rows=channel_rows,
                        use_memory=assembly_use_memory,
                    )
                except asyncio.CancelledError:
                    raise
                except Exception as exc:  # noqa: BLE001 - legacy is the rollback path
                    self._emit_retrieval_v2(
                        "v2",
                        "failed",
                        information.references,
                        selected_context="none",
                        fallback_reason="preflight_error",
                        retrieval_v2_error_type=type(exc).__name__,
                    )
                else:
                    if run.result is None:
                        self._emit_retrieval_v2(
                            "v2",
                            run.status,
                            information.references,
                            run,
                            selected_context="none",
                            fallback_reason=run.fallback_reason,
                        )
                    else:
                        self._emit_retrieval_v2(
                            "v2",
                            "completed",
                            information.references,
                            run,
                            selected_context="v2",
                        )
                        information = self._activate_retrieval_v2(
                            information,
                            run.result,
                        )
                        selected_rag = "v2"

        self.usage.routing_event(
            "rag.mode", rag_mode=rag_mode, rag_selected_context=selected_rag,
        )
        CURRENT_MEMORY_CONTEXT.set(tuple(build_memory_context(channel_rows, scope.user_id)))
        factual_recall_plan = plan_reference_gated_recall(
            assembly_store,
            scope,
            routing.visible_content,
            use_memory=use_memory,
            allow_cross_space=use_memory,
        )
        visual_inputs = CURRENT_VISUAL_INPUTS.get()
        context_chars, ambient_context_chars = self._routing_context_chars(
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
            ambient_context_chars=ambient_context_chars,
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
                        visual_inputs=visual_inputs,
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
                    ambient_context_chars=ambient_context_chars,
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
                    ambient_context_chars=ambient_context_chars,
                    visual_inputs=visual_inputs,
                )
                self._start_shadow_classification(
                    information,
                    model_plan,
                    context_chars=context_chars,
                    ambient_context_chars=ambient_context_chars,
                    visual_inputs=visual_inputs,
                )

        if model_plan is None:
            model_plan = build_model_plan(
                self.settings,
                information,
                context_chars=context_chars,
                ambient_context_chars=ambient_context_chars,
                visual_inputs=visual_inputs,
            )

        weather = None
        if information.route == InformationRoute.GENERAL:
            weather = self.ambient_weather.current(self.settings)
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

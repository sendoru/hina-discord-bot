from hina_bot.core.memory_context import CURRENT_EGRESS_DECISION

from .egress_policy import filter_channel_context, filter_public_context
from .information_pipeline import InformationPipeline
from .memory_summary import SHARED_SUMMARY_POLICY, SUMMARY_POLICY
from .prompts import load_prompt
from .providers import create_provider_client
from .routing_plan import build_routing_plan
from .vision import VISION_REQUEST_ACTIVE, wrap_vision_client

GENERAL_RP_OUTPUT_POLICY = load_prompt("general_rp_output.md")


class LLM(InformationPipeline):
    """Production LLM orchestrating routing, vision, memory, and RP policy."""

    def __init__(self, settings, client=None, classifier_client=None):
        primary_client = wrap_vision_client(
            client or create_provider_client(settings, settings.provider)
        )
        super().__init__(
            settings,
            client=primary_client,
            classifier_client=classifier_client,
        )
        self.character = self.character.rstrip() + "\n\n" + GENERAL_RP_OUTPUT_POLICY

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
    ) -> str:
        policy = self.settings.external_context_policy
        raw_channel_context = list(channel_context or ())
        raw_public_context = list(public_context or ())
        safe_channel_context = filter_channel_context(
            raw_channel_context,
            scope.user_id,
            policy,
        )
        safe_public_context = filter_public_context(
            raw_public_context,
            scope.user_id,
            policy,
        )
        egress_token = CURRENT_EGRESS_DECISION.set({
            "policy": policy,
            "channel_input": len(raw_channel_context),
            "channel_allowed": len(safe_channel_context),
            "channel_blocked": len(raw_channel_context) - len(safe_channel_context),
            "public_input": len(raw_public_context),
            "public_allowed": len(safe_public_context),
            "public_blocked": len(raw_public_context) - len(safe_public_context),
        })
        vision_token = VISION_REQUEST_ACTIVE.set(True)
        try:
            plan = build_routing_plan(
                store,
                scope,
                content,
                safe_channel_context,
                use_memory=use_memory,
                classifier_context_policy=policy,
            )
            return await super().answer(
                store,
                scope,
                name,
                content,
                public_context=safe_public_context,
                channel_context=safe_channel_context,
                emoji_catalog=emoji_catalog,
                use_memory=use_memory,
                routing_plan=plan,
            )
        finally:
            VISION_REQUEST_ACTIVE.reset(vision_token)
            CURRENT_EGRESS_DECISION.reset(egress_token)


__all__ = [
    "GENERAL_RP_OUTPUT_POLICY",
    "LLM",
    "SHARED_SUMMARY_POLICY",
    "SUMMARY_POLICY",
]

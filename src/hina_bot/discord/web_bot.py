import logging
import re
from contextvars import ContextVar
from datetime import timedelta

import discord

from hina_bot.ai.egress_policy import strict_policy
from hina_bot.ai.identity_resolution import identity_group, identity_resolution_needed
from hina_bot.ai.information_pipeline import LLM
from hina_bot.ai.vision import CURRENT_VISUAL_INPUTS
from hina_bot.core.config import Settings
from hina_bot.core.observability import CURRENT_TURN_ID, new_turn_id
from hina_bot.core.routing import Scope, trigger_text

from .bot import HinaClient as BaseHinaClient
from .chatlog_capture import capture_mode
from .reply_context import REPLY_CONTEXT, collect_reply_context
from .slash_commands import install_slash_commands
from .target_context import TARGET_CONTEXT, collect
from .target_recent import CURRENT_DIRECT_TRIGGER, TargetAwareRecentMessages
from .turn_provenance import CURRENT_TURN_PROVENANCE, build_turn_provenance
from .vision import VisionLimits, collect_visual_inputs

log = logging.getLogger("hina")

CURRENT_PUBLIC_CONTEXT_REQUEST = ContextVar(
    "current_public_context_request",
    default=(False, ()),
)
_PUBLIC_MEMORY_QUERY = re.compile(
    r"(?:기억(?:나|해|하고)|전에|저번|지난번|예전에|다른\s*(?:채널|방)|"
    r"서버(?:에서|의)|평소|원래|(?:말|얘기)했|어떤\s*(?:사람|애|유저)|"
    r"성격|인상|평판|어떻게\s*생각)",
    re.IGNORECASE,
)


def _target_history_visibility(store, scope, *, strict_egress: bool) -> str:
    if not store.chat_log_enabled(scope):
        return "off"
    if strict_egress or capture_mode(store, scope) == "direct":
        return "direct"
    return "all"


_BROAD_SERVER_MEMORY_QUERY = re.compile(
    r"(?:서버(?:에서|의).*(?:누가|누구|사람들|다른\s*사람)|"
    r"누가.*(?:말했|얘기했))",
    re.IGNORECASE,
)


def _augment_empty_call(content: str, text: str | None, has_visuals: bool) -> str | None:
    """Only add image intent when a bare trigger has strong visual context."""
    if text is None or text or not has_visuals:
        return None
    return (content + " 이 이미지나 스티커를 봐줘.").strip()


def _public_context_request(
    scope: Scope,
    text: str,
    sampled: list[dict],
    *,
    allow_cross_user: bool = True,
    resolved_user_ids=(),
):
    """Choose whether cross-channel public memory is relevant to this invocation.

    Ordinary chat should not receive unrelated users' summaries. Explicitly targeted user questions
    may use that target's public memory, while history/memory questions without a target default to
    the current user's own public calls. Only clearly broad server-history questions may fan out.
    A strict external-context policy disables cross-user fan-out entirely.
    """
    target_ids = tuple({
        *(
            int(item["user_id"])
            for item in sampled
            if str(item.get("user_id", "")).isdigit()
        ),
        *(
            int(user_id)
            for user_id in resolved_user_ids
            if str(user_id).isdigit()
        ),
    })
    if target_ids and allow_cross_user:
        return True, target_ids
    if not _PUBLIC_MEMORY_QUERY.search(text):
        return False, ()
    if (
        allow_cross_user
        and scope.guild_id is not None
        and _BROAD_SERVER_MEMORY_QUERY.search(text)
    ):
        return True, None
    return True, (scope.user_id,)


class HinaClient(BaseHinaClient):
    """Production Discord client wired to current context, web search, and vision."""

    def __init__(self, settings: Settings, *, store=None, llm=None):
        if llm is None:
            llm = LLM(settings)
        super().__init__(settings, store=store, llm=llm)
        self.recent = TargetAwareRecentMessages(
            budget=settings.channel_context_chars,
            store=self.store,
            external_context_policy=settings.external_context_policy,
        )
        self.vision_limits = VisionLimits.from_settings(settings)
        install_slash_commands(self)

    def _emit_identity_resolution(
        self,
        scope: Scope,
        *,
        outcome: str,
        resolver_invoked: bool,
        candidate_count: int = 0,
        raw_candidate_count: int = 0,
        reference: str = "",
        resolved_user_id: str = "",
        blocked_reason: str = "",
    ) -> None:
        fields: dict[str, object] = {
            "outcome": outcome,
            "resolver_invoked": resolver_invoked,
            "candidate_count": int(candidate_count),
            "raw_candidate_count": int(raw_candidate_count),
            "evidence_source": "resolver_derived" if resolver_invoked else "policy",
        }
        if blocked_reason:
            fields["blocked_reason"] = blocked_reason
        if scope.guild_id is not None and reference:
            group = identity_group(
                reference,
                guild_id=scope.guild_id,
                secret=self.settings.discord_token,
                kind="reference",
            )
            if group:
                fields["reference_group"] = group
        if scope.guild_id is not None and resolved_user_id:
            group = identity_group(
                resolved_user_id,
                guild_id=scope.guild_id,
                secret=self.settings.discord_token,
                kind="user",
            )
            if group:
                fields["resolved_user_group"] = group
        self.events.emit("identity.resolution", **fields)

    async def _resolve_text_targets(
        self,
        message,
        scope: Scope,
        text: str | None,
        *,
        strict_egress: bool,
        third_party_mention: bool,
    ):
        if text is None or scope.guild_id is None or not identity_resolution_needed(text):
            return [], ()

        if strict_egress:
            self._emit_identity_resolution(
                scope,
                outcome="blocked",
                resolver_invoked=False,
                blocked_reason="strict_egress",
            )
            return [], ()
        if third_party_mention:
            self._emit_identity_resolution(
                scope,
                outcome="blocked",
                resolver_invoked=False,
                blocked_reason="explicit_third_party_mention",
            )
            return [], ()
        if not hasattr(self.llm, "resolve_speaker_identity"):
            self._emit_identity_resolution(
                scope,
                outcome="blocked",
                resolver_invoked=False,
                blocked_reason="resolver_unavailable",
            )
            return [], ()

        raw_candidates = self.store.identity_candidates(
            scope.guild_id,
            exclude_user_ids={scope.user_id, self.user.id},
        )
        guild = message.guild
        candidates = []
        for candidate in raw_candidates:
            visible = False
            for channel_id in candidate.get("channel_ids", ()):
                channel = guild.get_channel(channel_id) if guild is not None else None
                if not isinstance(channel, discord.TextChannel):
                    continue
                public = channel.permissions_for(guild.default_role)
                caller = channel.permissions_for(message.author)
                if (
                    public.view_channel
                    and public.read_message_history
                    and caller.view_channel
                    and caller.read_message_history
                ):
                    visible = True
                    break
            if not visible:
                continue
            member = guild.get_member(int(candidate["user_id"])) if guild is not None else None
            if member is not None:
                names = candidate["names"]
                for value in (
                    getattr(member, "display_name", ""),
                    getattr(member, "global_name", ""),
                    getattr(member, "name", ""),
                ):
                    value = str(value or "").strip()
                    if value and value not in names and len(names) < 4:
                        names.append(value[:100])
            candidates.append(candidate)

        if not candidates:
            self._emit_identity_resolution(
                scope,
                outcome="blocked",
                resolver_invoked=False,
                candidate_count=0,
                raw_candidate_count=len(raw_candidates),
                blocked_reason="no_visible_candidates",
            )
            return [], ()

        resolution = await self.llm.resolve_speaker_identity(text, candidates)
        resolution_outcome = getattr(
            resolution,
            "status",
            "resolved" if resolution.resolved else "none",
        )
        self._emit_identity_resolution(
            scope,
            outcome=resolution_outcome,
            resolver_invoked=True,
            candidate_count=len(candidates),
            raw_candidate_count=len(raw_candidates),
            reference=getattr(resolution, "reference", ""),
            resolved_user_id=resolution.user_id if resolution.resolved else "",
        )
        if not resolution.resolved:
            return [], ()
        candidate = next(
            (
                row for row in candidates
                if str(row.get("user_id")) == resolution.user_id
            ),
            None,
        )
        if candidate is None:
            return [], ()
        return ([{
            "user_id": resolution.user_id,
            "name": candidate["names"][0] if candidate["names"] else "",
        }], (int(resolution.user_id),))

    async def public_sources(self, user_id: int, guild_id: int | None = None):
        enabled, requested_ids = CURRENT_PUBLIC_CONTEXT_REQUEST.get()
        if not enabled:
            return []
        if guild_id is None and not self.settings.public_memory_in_dm:
            return []

        requested = None if requested_ids is None else set(requested_ids)
        allowed, members = [], {}
        for source in self.store.public_candidates(user_id, guild_id):
            if requested is not None and source.user_id not in requested:
                continue
            if (
                self.settings.allowed_guild_ids
                and source.guild_id not in self.settings.allowed_guild_ids
            ):
                continue
            guild = self.get_guild(source.guild_id)
            if guild is None or guild.unavailable:
                continue
            channel = guild.get_channel(source.channel_id)
            if not isinstance(channel, discord.TextChannel):
                continue
            public = channel.permissions_for(guild.default_role)
            if not (public.view_channel and public.read_message_history):
                continue
            if source.guild_id not in members:
                try:
                    # Access is checked for the current caller, not for the source-message author.
                    members[source.guild_id] = await guild.fetch_member(user_id)
                except discord.HTTPException:
                    members[source.guild_id] = None
            member = members[source.guild_id]
            if member is None:
                continue
            permissions = channel.permissions_for(member)
            if not (permissions.view_channel and permissions.read_message_history):
                continue
            allowed.append(source)
            if len(allowed) == 4:
                break
        return allowed

    async def hydrate_recent_history(self, message, scope):
        """Backfill the bounded history while respecting the active chatlog mode."""
        if scope.guild_id is None or not self.recent.needs_hydration(scope):
            return
        created_at = getattr(message, "created_at", None)
        history = getattr(message.channel, "history", None)
        if created_at is None or history is None:
            self.recent.mark_hydrated(scope)
            return

        after = created_at - timedelta(seconds=self.recent.ttl)
        policy = capture_mode(self.store, scope)
        try:
            async for old in history(
                limit=self.recent.limit,
                before=message,
                after=after,
                oldest_first=False,
            ):
                if old.webhook_id is not None:
                    continue
                own_bot = self.user is not None and old.author.id == self.user.id
                other_bot = bool(old.author.bot) and not own_bot
                historical_text = (
                    None
                    if own_bot
                    else trigger_text(
                        old,
                        self.user.id,
                        self.settings.dm_always_reply,
                        self.settings.call_prefixes,
                    )
                )
                if policy == "direct" and not own_bot and historical_text is None:
                    continue
                if not old.content:
                    continue
                historical_scope = Scope(
                    scope.guild_id,
                    scope.channel_id,
                    old.author.id,
                    scope.public_at_capture,
                )
                direct_token = CURRENT_DIRECT_TRIGGER.set(historical_text is not None)
                try:
                    self.recent.add(
                        historical_scope,
                        old.id,
                        old.author.display_name,
                        old.content,
                        role="assistant" if own_bot else ("bot" if other_bot else "user"),
                        unix_time=old.created_at.timestamp(),
                        author_user_id=old.author.id if own_bot else None,
                    )
                finally:
                    CURRENT_DIRECT_TRIGGER.reset(direct_token)
        except discord.HTTPException as exc:
            log.warning("Recent channel history backfill failed (%s)", type(exc).__name__)
            return
        self.recent.mark_hydrated(scope)

    async def on_message(self, message):
        if self.user is None:
            return await super().on_message(message)
        text = trigger_text(
            message,
            self.user.id,
            self.settings.dm_always_reply,
            self.settings.call_prefixes,
        )
        scope = Scope(
            message.guild.id if message.guild else None,
            message.channel.id,
            message.author.id,
        )

        own_bot = message.author.id == self.user.id
        if own_bot or message.webhook_id is not None:
            return

        # Passive messages from other bots remain channel context only in chatlog `all` mode.
        # An explicit mention/reply ping is different: it is a real invocation and continues through
        # the normal request pipeline, while prefixes and DM auto-reply remain human-only.
        if message.author.bot and text is None:
            if (
                scope.guild_id is not None
                and self.store.chat_log_enabled(scope)
                and capture_mode(self.store, scope) == "all"
                and message.content
            ):
                self.recent.add(
                    scope,
                    message.id,
                    message.author.display_name,
                    message.content,
                    role="bot",
                )
            return

        strict_egress = strict_policy(self.settings.external_context_policy)
        third_party_mention = any(
            getattr(user, "id", None) not in {self.user.id, scope.user_id}
            and not getattr(user, "bot", False)
            for user in getattr(message, "mentions", ())
        )

        identity_turn_id = (
            new_turn_id()
            if text is not None
            and scope.guild_id is not None
            and identity_resolution_needed(text)
            else None
        )
        identity_token = (
            CURRENT_TURN_ID.set(identity_turn_id)
            if identity_turn_id is not None
            else None
        )
        try:
            resolved_targets, resolved_user_ids = await self._resolve_text_targets(
                message,
                scope,
                text,
                strict_egress=strict_egress,
                third_party_mention=third_party_mention,
            )
        finally:
            if identity_token is not None:
                CURRENT_TURN_ID.reset(identity_token)

        target_visibility = _target_history_visibility(
            self.store,
            scope,
            strict_egress=strict_egress,
        )
        sampled = (
            await collect(
                message,
                self.user.id,
                text,
                visibility_mode=target_visibility,
                call_prefixes=self.settings.call_prefixes,
                extra_targets=resolved_targets,
            )
            if text is not None
            else []
        )
        replied = (
            await collect_reply_context(
                message,
                self.user.id,
                allowed_author_id={scope.user_id, self.user.id} if strict_egress else None,
            )
            if text is not None
            else []
        )
        reply_chain_visual_ids = self.recent.reply_chain_visual_ids(scope, replied)

        visual_capture_mode = capture_mode(self.store, scope)

        def recent_visual_allowed(candidate) -> bool:
            if getattr(candidate, "webhook_id", None) is not None:
                return False
            author = getattr(candidate, "author", None)
            author_id = getattr(author, "id", None)
            if author is None or author_id is None:
                return False
            own_visual = author_id == self.user.id
            if own_visual:
                return True
            direct = trigger_text(
                candidate,
                self.user.id,
                self.settings.dm_always_reply,
                self.settings.call_prefixes,
            ) is not None
            # Passive bot images never become visual context. A bot's own explicit call may carry
            # an image, and is bounded by the same direct-call provenance as its text.
            if getattr(author, "bot", False):
                return direct
            if strict_egress:
                return direct
            if visual_capture_mode == "direct":
                return direct
            return True

        visuals = (
            await collect_visual_inputs(
                message,
                limits=self.vision_limits,
                include_reply=True,
                include_recent=self.store.chat_log_enabled(scope),
                allowed_reply_author_id=(
                    {scope.user_id, self.user.id} if strict_egress else None
                ),
                context_message_ids=reply_chain_visual_ids,
                allowed_context_author_id=scope.user_id if strict_egress else None,
                recent_filter=recent_visual_allowed,
            )
            if text is not None else []
        )
        public_request = (
            (
                (False, ())
                if strict_egress and third_party_mention
                else _public_context_request(
                    scope,
                    text,
                    sampled,
                    allow_cross_user=not strict_egress,
                    resolved_user_ids=resolved_user_ids,
                )
            )
            if text is not None
            else (False, ())
        )
        target_token = TARGET_CONTEXT.set(tuple(sampled))
        reply_token = REPLY_CONTEXT.set(tuple(replied))
        visual_token = CURRENT_VISUAL_INPUTS.set(tuple(visuals))
        provenance_token = CURRENT_TURN_PROVENANCE.set(
            build_turn_provenance(message, text or "", replied, visuals)
            if text is not None
            else None
        )
        public_token = CURRENT_PUBLIC_CONTEXT_REQUEST.set(public_request)
        direct_token = CURRENT_DIRECT_TRIGGER.set(text is not None)

        # A text-only bare call stays a bare call and uses the base client's relationship-aware
        # fixed reply. Current-message or explicit-reply visuals are strong enough to infer an
        # image request; passive recent images alone must not turn a bare call into one.
        original_content = None
        strong_visuals = any(
            visual.reference_strength in {"current_message", "explicit_reply"}
            for visual in visuals
        )
        augmented_content = _augment_empty_call(message.content, text, strong_visuals)
        if augmented_content is not None:
            original_content = message.content
            message.content = augmented_content
        correlation_token = (
            CURRENT_TURN_ID.set(identity_turn_id)
            if identity_turn_id is not None
            else None
        )
        try:
            return await super().on_message(message)
        finally:
            if correlation_token is not None:
                CURRENT_TURN_ID.reset(correlation_token)
            if original_content is not None:
                message.content = original_content
            CURRENT_DIRECT_TRIGGER.reset(direct_token)
            CURRENT_PUBLIC_CONTEXT_REQUEST.reset(public_token)
            CURRENT_VISUAL_INPUTS.reset(visual_token)
            CURRENT_TURN_PROVENANCE.reset(provenance_token)
            REPLY_CONTEXT.reset(reply_token)
            TARGET_CONTEXT.reset(target_token)


def main():
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
    log.setLevel(logging.INFO)
    try:
        settings = Settings.load()
    except ValueError as exc:
        raise SystemExit(str(exc)) from None
    bot = HinaClient(settings)
    bot.run(settings.discord_token, log_handler=None)


if __name__ == "__main__":
    main()

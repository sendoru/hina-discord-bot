import asyncio
import logging
import re
import time
from contextvars import ContextVar
from datetime import timedelta

import discord

from hina_bot.ai.egress_policy import strict_policy
from hina_bot.ai.identity_resolution import (
    SpeakerIdentityCandidate,
    identity_group,
    identity_resolution_needed,
    match_identity_aliases,
    narrow_identity_candidates,
)
from hina_bot.ai.vision import CURRENT_VISUAL_INPUTS
from hina_bot.core.config import Settings
from hina_bot.core.identity_context import CURRENT_RESOLVED_IDENTITIES
from hina_bot.core.interaction_context import CURRENT_INTERACTION_CONTEXT
from hina_bot.core.observability import CURRENT_TURN_ID, new_turn_id
from hina_bot.core.routing import Scope, trigger_text

from .bot import HinaClient as BaseHinaClient
from .chatlog_capture import capture_mode
from .interaction_context import build_interaction_context
from .member_directory import (
    current_member_directory,
    historical_identity_candidates,
    member_names,
    visible_member,
)
from .reply_context import REPLY_CONTEXT, collect_reply_context
from .slash_commands import install_slash_commands
from .target_context import TARGET_CONTEXT, collect, retrieval_mode
from .target_recent import CURRENT_CHANNEL_CONTEXT, TargetAwareRecentMessages
from .turn_provenance import CURRENT_TURN_PROVENANCE, build_turn_provenance
from .vision import (
    VisionLimits,
    collect_visual_inputs,
    message_has_visual,
    rank_visual_context_refs,
)

log = logging.getLogger("hina")

CURRENT_PUBLIC_CONTEXT_REQUEST = ContextVar(
    "current_public_context_request",
    default=(False, ()),
)
async def _timed(awaitable):
    started = time.perf_counter()
    result = await awaitable
    return result, round((time.perf_counter() - started) * 1000)


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
        mode = retrieval_mode(text)
        # Resolving a person authorizes identity only. Recent speech stays channel-local;
        # calling/pinging someone must not load that person's public summaries/history.
        if mode == "basic":
            return False, ()
        if mode == "deep" or _PUBLIC_MEMORY_QUERY.search(text) or identity_resolution_needed(text):
            return True, target_ids
        return False, ()
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

    def __init__(self, settings: Settings, *, store=None, llm):
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
        resolution_method: str = "policy",
        directory_complete: bool = False,
    ) -> None:
        fields: dict[str, object] = {
            "outcome": outcome,
            "resolver_invoked": resolver_invoked,
            "candidate_count": int(candidate_count),
            "raw_candidate_count": int(raw_candidate_count),
            "evidence_source": "resolver_derived" if resolver_invoked else "policy",
            "resolution_method": resolution_method,
            "directory_complete": directory_complete,
        }
        if resolution_method == "exact":
            fields["evidence_source"] = "member_directory"
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
        if not text or scope.guild_id is None:
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
        if (self.settings.allowed_guild_ids
                and scope.guild_id not in self.settings.allowed_guild_ids):
            return [], ()
        if not isinstance(getattr(message, "channel", None), discord.TextChannel):
            return [], ()

        directory = current_member_directory(
            message, self.user.id, members_intent=self.intents.members,
        )
        guild = message.guild
        if guild is None or getattr(guild, "unavailable", False):
            return [], ()
        if message.channel.permissions_for(message.author).view_channel is not True:
            return [], ()

        def objects(rows):
            return tuple(
                SpeakerIdentityCandidate(str(row["user_id"]), tuple(row["names"]))
                for row in rows
            )

        exact = match_identity_aliases(text, objects(directory.candidates))
        if exact.status == "ambiguous":
            self._emit_identity_resolution(
                scope, outcome="ambiguous", resolver_invoked=False,
                raw_candidate_count=len(directory.candidates), resolution_method="exact",
                reference=exact.reference,
                directory_complete=directory.complete,
            )
            return [], ()

        if exact.resolved and directory.complete:
            resolution = exact
            candidates = [row for row in directory.candidates if row["user_id"] == exact.user_id]
            raw_count = len(directory.candidates)
            method = "exact"
        else:
            historical = historical_identity_candidates(message, self.user.id, self.store)
            if directory.complete:
                by_user = {row["user_id"]: dict(row) for row in directory.candidates}
                for row in historical:
                    current = by_user.get(row["user_id"])
                    if current is not None:
                        current["names"] = list(dict.fromkeys(
                            [*current["names"], *row["historical_names"]]
                        ))[:4]
                source = list(by_user.values())
            else:
                # A partial cache cannot establish unique aliases. Retain the bounded historical
                # fallback, with a live membership/visibility check before its provider call.
                source = historical
            narrowed = narrow_identity_candidates(text, objects(source))
            raw_count = len(source)
            ids = {row.user_id for row in narrowed}
            candidates = [row for row in source if row["user_id"] in ids]
            if len(candidates) > (32 if directory.complete else 8):
                self._emit_identity_resolution(
                    scope, outcome="ambiguous", resolver_invoked=False,
                    raw_candidate_count=raw_count, blocked_reason="candidate_limit",
                    directory_complete=directory.complete,
                )
                return [], ()
            if candidates and not directory.complete:
                candidates = await self._verify_historical_identities(message, candidates)
                verified_ids = {
                    row.user_id for row in narrow_identity_candidates(text, objects(candidates))
                }
                candidates = [row for row in candidates if row["user_id"] in verified_ids]
            if not candidates:
                self._emit_identity_resolution(
                    scope, outcome="blocked", resolver_invoked=False,
                    raw_candidate_count=raw_count,
                    blocked_reason=(
                        "directory_incomplete" if exact.resolved else "no_relevant_candidates"
                    ),
                    directory_complete=directory.complete,
                )
                return [], ()
            if not hasattr(self.llm, "resolve_speaker_identity"):
                self._emit_identity_resolution(
                    scope, outcome="blocked", resolver_invoked=False,
                    blocked_reason="resolver_unavailable", directory_complete=directory.complete,
                )
                return [], ()
            async with self.slots:
                resolution = await self.llm.resolve_speaker_identity(text, candidates)
            method = "semantic"

        resolution_outcome = getattr(
            resolution,
            "status",
            "resolved" if resolution.resolved else "none",
        )
        self._emit_identity_resolution(
            scope,
            outcome=resolution_outcome,
            resolver_invoked=method == "semantic",
            candidate_count=len(candidates),
            raw_candidate_count=raw_count,
            resolution_method=method,
            directory_complete=directory.complete,
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
            "names": list(candidate["names"][:4]),
            "reference": getattr(resolution, "reference", ""),
        }], (int(resolution.user_id),))

    async def _verify_historical_identities(self, message, candidates):
        async def verify(row):
            fetch_member = getattr(message.guild, "fetch_member", None)
            if fetch_member is None:
                return None
            try:
                async with self.slots:
                    member = await asyncio.wait_for(
                        fetch_member(int(row["user_id"])),
                        timeout=min(float(self.settings.routing_classifier_timeout_seconds), 4.0),
                    )
            except (TimeoutError, discord.HTTPException):
                return None
            if (str(member.id) != row["user_id"]
                    or not visible_member(message, member, self.user.id)):
                return None
            return {
                "user_id": row["user_id"],
                "names": list(dict.fromkeys(
                    [*member_names(member), *row["historical_names"]]
                ))[:4],
            }

        return [row for row in await asyncio.gather(*(verify(row) for row in candidates)) if row]

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
                        self.settings.always_reply_channel_ids,
                    )
                )
                if policy == "direct" and not own_bot and historical_text is None:
                    continue
                has_visual = message_has_visual(old)
                if not old.content and not has_visual:
                    continue
                historical_scope = Scope(
                    scope.guild_id,
                    scope.channel_id,
                    old.author.id,
                    scope.public_at_capture,
                )
                self.recent.add(
                    historical_scope,
                    old.id,
                    old.author.display_name,
                    old.content,
                    role="assistant" if own_bot else ("bot" if other_bot else "user"),
                    unix_time=old.created_at.timestamp(),
                    author_user_id=old.author.id,
                    reply_target_user_id=None,
                    direct_trigger=None if own_bot else historical_text is not None,
                    has_visual=has_visual,
                    capture_turn_provenance=False,
                )
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
            self.settings.always_reply_channel_ids,
        )
        scope = Scope(
            message.guild.id if message.guild else None,
            message.channel.id,
            message.author.id,
        )

        own_bot = message.author.id == self.user.id
        if own_bot or message.webhook_id is not None:
            return

        guild = getattr(message, "guild", None)
        channel = getattr(message, "channel", None)
        guild_name = getattr(guild, "name", None)
        channel_name = getattr(channel, "name", None)
        if (
            guild is not None
            and channel is not None
            and (
                not self.settings.allowed_guild_ids
                or guild.id in self.settings.allowed_guild_ids
            )
            and isinstance(guild_name, str)
            and isinstance(channel_name, str)
        ):
            self.store.observe_guild_channel(
                guild.id,
                guild_name,
                channel.id,
                channel_name,
            )

        # Passive messages from other bots remain channel context only in chatlog `all` mode.
        # Mention/reply pings remain explicit invocations everywhere; configured always-reply
        # channels additionally accept call prefixes from bots. The implicit always-reply behavior
        # itself remains human-only, and bot-authored DMs stay disabled.
        if message.author.bot and text is None:
            if (
                scope.guild_id is not None
                and self.store.chat_log_enabled(scope)
                and capture_mode(self.store, scope) == "all"
                and (message.content or message_has_visual(message))
            ):
                self.recent.add(
                    scope,
                    message.id,
                    message.author.display_name,
                    message.content,
                    role="bot",
                    has_visual=message_has_visual(message),
                )
            return

        preflight_started = time.perf_counter() if text is not None else None
        preflight_turn_id = new_turn_id() if text is not None else None

        strict_egress = strict_policy(self.settings.external_context_policy)
        third_party_mention = any(
            getattr(user, "id", None) not in {self.user.id, scope.user_id}
            and not getattr(user, "bot", False)
            for user in getattr(message, "mentions", ())
        )

        identity_started = time.perf_counter()
        identity_token = (
            CURRENT_TURN_ID.set(preflight_turn_id)
            if preflight_turn_id is not None
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
        identity_ms = round((time.perf_counter() - identity_started) * 1000)

        target_visibility = _target_history_visibility(
            self.store,
            scope,
            strict_egress=strict_egress,
        )
        if text is not None:
            target_result, reply_result = await asyncio.gather(
                _timed(
                    collect(
                        message,
                        self.user.id,
                        text,
                        visibility_mode=target_visibility,
                        call_prefixes=self.settings.call_prefixes,
                        always_reply_channel_ids=self.settings.always_reply_channel_ids,
                        extra_targets=resolved_targets,
                    )
                ),
                _timed(
                    collect_reply_context(
                        message,
                        self.user.id,
                        allowed_author_id=(
                            {scope.user_id, self.user.id} if strict_egress else None
                        ),
                    )
                ),
            )
            sampled, target_context_ms = target_result
            replied, reply_context_ms = reply_result
        else:
            sampled = []
            replied = []
            target_context_ms = 0
            reply_context_ms = 0

        visual_context_started = time.perf_counter()
        selected_context = []
        history_hydration_ms = 0
        history_hydration_needed = False
        channel_context_select_ms = 0
        if (
            text is not None
            and scope.guild_id is not None
            and self.store.chat_log_enabled(scope)
        ):
            target_selection_token = TARGET_CONTEXT.set(tuple(sampled))
            reply_selection_token = REPLY_CONTEXT.set(tuple(replied))
            try:
                history_hydration_needed = self.recent.needs_hydration(scope)
                history_hydration_started = time.perf_counter()
                await self.hydrate_recent_history(message, scope)
                history_hydration_ms = round(
                    (time.perf_counter() - history_hydration_started) * 1000
                )
                channel_context_select_started = time.perf_counter()
                selected_context = self.recent.context(scope, message.id)
                channel_context_select_ms = round(
                    (time.perf_counter() - channel_context_select_started) * 1000
                )
            finally:
                REPLY_CONTEXT.reset(reply_selection_token)
                TARGET_CONTEXT.reset(target_selection_token)

        visual_ref_select_started = time.perf_counter()
        visual_refs = rank_visual_context_refs(selected_context)
        visual_ref_select_ms = round(
            (time.perf_counter() - visual_ref_select_started) * 1000
        )
        visual_fetch_started = time.perf_counter()
        visuals = (
            await collect_visual_inputs(
                message,
                context_refs=visual_refs,
                limits=self.vision_limits,
            )
            if text is not None
            else []
        )
        visual_fetch_ms = round((time.perf_counter() - visual_fetch_started) * 1000)
        visual_context_ms = round((time.perf_counter() - visual_context_started) * 1000)
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
        if preflight_started is not None and preflight_turn_id is not None:
            event_token = CURRENT_TURN_ID.set(preflight_turn_id)
            try:
                self.events.emit(
                    "turn.preflight",
                    scope="guild" if scope.guild_id is not None else "dm",
                    preflight_ms=round((time.perf_counter() - preflight_started) * 1000),
                    identity_ms=identity_ms,
                    target_context_ms=target_context_ms,
                    reply_context_ms=reply_context_ms,
                    visual_context_ms=visual_context_ms,
                    history_hydration_ms=history_hydration_ms,
                    history_hydration_needed=history_hydration_needed,
                    channel_context_select_ms=channel_context_select_ms,
                    channel_context_count=len(selected_context),
                    visual_ref_select_ms=visual_ref_select_ms,
                    visual_ref_count=len(visual_refs),
                    visual_fetch_ms=visual_fetch_ms,
                    visual_input_count=len(visuals),
                )
            finally:
                CURRENT_TURN_ID.reset(event_token)

        interaction_token = CURRENT_INTERACTION_CONTEXT.set(
            build_interaction_context(
                message,
                self.user.id,
                replied,
                include_mention_names=not strict_egress,
            )
        )
        target_token = TARGET_CONTEXT.set(tuple(sampled))
        reply_token = REPLY_CONTEXT.set(tuple(replied))
        channel_context_token = CURRENT_CHANNEL_CONTEXT.set(tuple(selected_context))
        visual_token = CURRENT_VISUAL_INPUTS.set(tuple(visuals))
        provenance_token = CURRENT_TURN_PROVENANCE.set(
            build_turn_provenance(message, text or "", replied, visuals)
            if text is not None
            else None
        )
        public_token = CURRENT_PUBLIC_CONTEXT_REQUEST.set(public_request)
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
            CURRENT_TURN_ID.set(preflight_turn_id)
            if preflight_turn_id is not None
            else None
        )
        resolved_identity_token = CURRENT_RESOLVED_IDENTITIES.set(tuple(resolved_targets))
        try:
            return await super().on_message(message)
        finally:
            if correlation_token is not None:
                CURRENT_TURN_ID.reset(correlation_token)
            if original_content is not None:
                message.content = original_content
            CURRENT_PUBLIC_CONTEXT_REQUEST.reset(public_token)
            CURRENT_VISUAL_INPUTS.reset(visual_token)
            CURRENT_TURN_PROVENANCE.reset(provenance_token)
            CURRENT_CHANNEL_CONTEXT.reset(channel_context_token)
            REPLY_CONTEXT.reset(reply_token)
            TARGET_CONTEXT.reset(target_token)
            CURRENT_RESOLVED_IDENTITIES.reset(resolved_identity_token)
            CURRENT_INTERACTION_CONTEXT.reset(interaction_token)

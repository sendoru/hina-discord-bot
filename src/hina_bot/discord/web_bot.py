import asyncio
import logging
import re
import time
from contextlib import nullcontext
from datetime import UTC, datetime, timedelta

import discord

from hina_bot.ai.audio import (
    CURRENT_AUDIO_INPUTS,
    CURRENT_AUDIO_UNAVAILABLE,
    OPENROUTER_AUDIO_FORMATS,
)
from hina_bot.ai.egress_policy import strict_policy
from hina_bot.ai.local_tools import CURRENT_LOCAL_TOOLS, LocalToolRegistry
from hina_bot.ai.vision import CURRENT_VISUAL_INPUTS
from hina_bot.core.config import Settings
from hina_bot.core.interaction_context import CURRENT_INTERACTION_CONTEXT
from hina_bot.core.observability import CURRENT_TURN_ID, new_turn_id
from hina_bot.core.routing import Scope, trigger_text
from hina_bot.core.server_memory_selection import CURRENT_SERVER_READER

from .audio import (
    AudioLimits,
    audio_context_refs,
    collect_audio_inputs,
    message_has_audio,
)
from .bot import CURRENT_TYPING_ACTIVE, CURRENT_TYPING_STOP
from .bot import HinaClient as BaseHinaClient
from .chat_history_tool import register_chat_history_tool
from .chatlog_capture import capture_mode
from .embed_context import message_embed_text
from .forward_context import message_context_text, message_turn_text
from .interaction_context import build_interaction_context
from .member_directory import channel_member_tool_registry
from .member_relationship import register_member_relationship
from .memory_recall_tool import register_memory_recall_tool
from .reply_context import REPLY_CONTEXT, collect_reply_context
from .server_memory import server_reader
from .slash_commands import install_slash_commands
from .target_context import TARGET_CONTEXT, collect
from .target_recent import CURRENT_CHANNEL_CONTEXT, TargetAwareRecentMessages
from .turn_provenance import CURRENT_TURN_PROVENANCE, build_turn_provenance
from .vision import (
    VisionFetchCache,
    VisionLimits,
    collect_visual_inputs,
    message_has_visual,
    rank_visual_context_refs,
)

log = logging.getLogger("hina")

class _ManagedTyping:
    """Stop typing before the first reply send, or on request exit."""

    def __init__(self, context):
        self.context = context
        self.active = False

    async def __aenter__(self):
        await self.context.__aenter__()
        self.active = True
        return self

    async def stop(self):
        if self.active:
            self.active = False
            await self.context.__aexit__(None, None, None)

    async def __aexit__(self, exc_type, exc, tb):
        await self.stop()


async def _timed(awaitable):
    started = time.perf_counter()
    result = await awaitable
    return result, round((time.perf_counter() - started) * 1000)


def _target_history_visibility(store, scope, *, strict_egress: bool) -> str:
    if not store.chat_log_enabled(scope):
        return "off"
    if strict_egress or capture_mode(store, scope) == "direct":
        return "direct"
    return "all"


class HinaClient(BaseHinaClient):
    """Production Discord client wired to current context, web search, and vision."""

    def __init__(self, settings: Settings, *, store=None, llm):
        super().__init__(settings, store=store, llm=llm)
        self.recent = TargetAwareRecentMessages(
            budget=settings.channel_context_chars,
            ttl=settings.recent_context_window_minutes * 60,
            store=self.store,
            external_context_policy=settings.external_context_policy,
        )
        self.audio_limits = AudioLimits.from_settings(settings)
        self.visual_cache = VisionFetchCache()
        install_slash_commands(self)

    async def on_message_edit(self, before, after):
        """Update only already-admitted recent rows when Discord resolves an embed.

        Edits do not create conversation turns or regenerate a bot reply. Context
        eligibility and capture mode were decided by the original on_message.
        """
        guild = getattr(after, "guild", None)
        channel = getattr(after, "channel", None)
        author = getattr(after, "author", None)
        if guild is not None and channel is not None and author is not None:
            scope = Scope(guild.id, channel.id, author.id)
            rows = self.recent.buffers.get(self.recent._key(scope), ())
            for row in rows:
                if (
                    str(row.get("message_id")) == str(getattr(after, "id", ""))
                    and str(row.get("author_user_id")) == str(author.id)
                ):
                    row["content"] = message_context_text(after)[:4000]
                    row["has_visual"] = message_has_visual(after)
                    row["has_audio"] = message_has_audio(after)
                    break
        if channel is not None:
            self.visual_cache.invalidate(channel.id, getattr(after, "id", None))

    @staticmethod
    async def _refresh_missing_embed(message):
        """One bounded Discord-only reread; never wait/poll or visit the linked site."""
        if getattr(message, "embeds", None) or not re.search(
            r"https?://", str(getattr(message, "content", "") or "")
        ):
            return message
        channel = getattr(message, "channel", None)
        fetch = getattr(channel, "fetch_message", None)
        if not callable(fetch):
            return message
        try:
            refreshed = await fetch(message.id)
        except (discord.HTTPException, ValueError, TypeError):
            return message
        if (
            getattr(refreshed, "id", None) == getattr(message, "id", None)
            and getattr(getattr(refreshed, "author", None), "id", None)
                == getattr(getattr(message, "author", None), "id", None)
        ):
            return refreshed
        return message

    async def on_raw_message_edit(self, payload):
        self.visual_cache.invalidate(payload.channel_id, payload.message_id)

    async def on_raw_message_delete(self, payload):
        self.visual_cache.invalidate(payload.channel_id, payload.message_id)
        # A user who deletes a message also withdraws the photo still waiting for memory
        # extraction. Edits are left alone: Discord also sends them when a link embed
        # resolves, so they say nothing about the attachment.
        self.store.discard_pending_visuals_for_message(payload.channel_id, payload.message_id)
        self.store.forget_server_memory_source(
            getattr(payload, "guild_id", None), payload.channel_id, payload.message_id
        )

    async def on_raw_bulk_message_delete(self, payload):
        for message_id in payload.message_ids:
            self.visual_cache.invalidate(payload.channel_id, message_id)
            self.store.discard_pending_visuals_for_message(payload.channel_id, message_id)
            self.store.forget_server_memory_source(
                getattr(payload, "guild_id", None), payload.channel_id, message_id
            )

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
        cutoff_id = self.store.latest_conversation_reset_cutoff()
        if cutoff_id:
            after = max(after, discord.utils.snowflake_time(cutoff_id))
        policy = capture_mode(self.store, scope)
        try:
            historical = []
            async for old in history(
                limit=self.recent.limit,
                before=message,
                after=after,
                oldest_first=False,
            ):
                historical.append(old)
            derived_replies = self.store.chat_history_reply_metadata(
                scope, [old.id for old in historical if old.author.id == self.user.id],
            )
            for old in historical:
                if cutoff_id and old.id <= cutoff_id:
                    continue
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
                has_audio = message_has_audio(old)
                if not message_context_text(old) and not has_visual and not has_audio:
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
                    message_context_text(old),
                    role="assistant" if own_bot else ("bot" if other_bot else "user"),
                    unix_time=old.created_at.timestamp(),
                    author_user_id=old.author.id,
                    reply_target_user_id=None,
                    direct_trigger=None if own_bot else historical_text is not None,
                    has_visual=has_visual,
                    has_audio=has_audio,
                    capture_turn_provenance=False,
                )
                if str(old.id) in derived_replies:
                    for row in self.recent.buffers.get(self.recent._key(scope), ()):
                        if row['message_id'] == old.id:
                            row['provenance_class'] = 'reference_derived'
                            row['history_lookup'] = derived_replies[str(old.id)]
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
        if text is not None:
            text = message_turn_text(message, text)
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
                and (message_context_text(message) or message_has_visual(message)
                     or message_has_audio(message))
            ):
                self.recent.add(
                    scope,
                    message.id,
                    message.author.display_name,
                    message_context_text(message),
                    role="bot",
                    has_visual=message_has_visual(message),
                    has_audio=message_has_audio(message),
                )
            return

        if (text == "" and getattr(message, "reference", None) is None
                and not message_has_visual(message)
                and not (self.settings.audio_input_enabled
                         and self.settings.provider == "gemini"
                         and message_has_audio(message))
                and not message_embed_text(message)):
            # A bare call without a current or explicit-reply visual always takes
            # the fixed-reply path. Do not fetch old visual context for it.
            token = CURRENT_TURN_ID.set(new_turn_id())
            try:
                self.events.emit(
                    "turn.preflight",
                    scope="guild" if scope.guild_id is not None else "dm",
                    preflight_ms=0,
                    preflight_fast_path=True,
                    typing_start_ms=None,
                    gateway_lag_ms=self._gateway_lag_ms(message),
                    target_context_ms=0,
                    reply_context_ms=0,
                    visual_context_ms=0,
                    history_hydration_ms=0,
                    history_hydration_needed=False,
                    channel_context_select_ms=0,
                    channel_context_count=0,
                    visual_ref_select_ms=0,
                    visual_ref_count=0,
                    visual_fetch_ms=0,
                    visual_input_count=0,
                )
                return await super().on_message(message)
            finally:
                CURRENT_TURN_ID.reset(token)

        preflight_started = time.perf_counter() if text is not None else None
        should_type = (
            text is not None
            and not self.stopping
            and (
                scope.guild_id is None
                or not self.settings.allowed_guild_ids
                or scope.guild_id in self.settings.allowed_guild_ids
            )
        )
        typing_factory = getattr(message.channel, "typing", None)
        use_typing = should_type and callable(typing_factory)
        managed = _ManagedTyping(typing_factory()) if use_typing else nullcontext()
        async with managed as session:
            typing_start_ms = (
                round((time.perf_counter() - preflight_started) * 1000)
                if use_typing and preflight_started is not None
                else None
            )
            token = CURRENT_TYPING_ACTIVE.set(True) if use_typing else None
            stop_token = CURRENT_TYPING_STOP.set(session.stop) if use_typing else None
            try:
                capture = (self.store.track_server_turn(scope, message.id)
                           if text is not None and not message.author.bot else nullcontext())
                with capture:
                    return await self._preflight_on_message(
                        message, text, scope, preflight_started, typing_start_ms
                    )
            finally:
                if stop_token is not None:
                    CURRENT_TYPING_STOP.reset(stop_token)
                if token is not None:
                    CURRENT_TYPING_ACTIVE.reset(token)

    @staticmethod
    def _gateway_lag_ms(message):
        created_at = getattr(message, "created_at", None)
        if not isinstance(created_at, datetime) or created_at.tzinfo is None:
            return None
        return max(0, round((datetime.now(UTC) - created_at).total_seconds() * 1000))

    async def _preflight_on_message(
        self, message, text, scope, preflight_started, typing_start_ms
    ):
        preflight_turn_id = new_turn_id() if text is not None else None
        if text is not None:
            message = await self._refresh_missing_embed(message)
        strict_egress = strict_policy(self.settings.external_context_policy)
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
        visual_diagnostics: dict[str, int] = {}
        visuals = (
            await collect_visual_inputs(
                message,
                context_refs=visual_refs,
                limits=VisionLimits.from_settings(self.settings),
                cache=self.visual_cache,
                diagnostics=visual_diagnostics,
            )
            if text is not None
            else []
        )
        visual_fetch_ms = round((time.perf_counter() - visual_fetch_started) * 1000)
        audio_fetch_started = time.perf_counter()
        audio_refs = audio_context_refs([*replied, *selected_context])
        unavailable_audio = bool(
            text is not None and self.settings.audio_input_enabled
            and self.settings.provider not in {"gemini", "openrouter"}
            and (message_has_audio(message) or audio_refs)
        )
        audio_inputs = (
            await collect_audio_inputs(
                message,
                context_refs=audio_refs,
                limits=self.audio_limits,
            )
            if (text is not None and self.settings.audio_input_enabled
                and self.settings.provider in {"gemini", "openrouter"})
            else []
        )
        if self.settings.provider == "openrouter" and audio_inputs:
            supported = [item for item in audio_inputs if item.mime_type in OPENROUTER_AUDIO_FORMATS]
            unavailable_audio = len(supported) != len(audio_inputs)
            audio_inputs = supported
        audio_fetch_ms = round((time.perf_counter() - audio_fetch_started) * 1000)
        visual_context_ms = round((time.perf_counter() - visual_context_started) * 1000)
        if preflight_started is not None and preflight_turn_id is not None:
            event_token = CURRENT_TURN_ID.set(preflight_turn_id)
            try:
                self.events.emit(
                    "turn.preflight",
                    scope="guild" if scope.guild_id is not None else "dm",
                    preflight_ms=round((time.perf_counter() - preflight_started) * 1000),
                    preflight_fast_path=False,
                    typing_start_ms=typing_start_ms,
                    gateway_lag_ms=self._gateway_lag_ms(message),
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
                    visual_diagnostics=visual_diagnostics,
                    audio_unsupported_provider=unavailable_audio,
                    audio_input_count=len(audio_inputs),
                    audio_duration_seconds=round(sum(a.duration_seconds for a in audio_inputs), 1),
                    audio_input_bytes=sum(len(a.data) for a in audio_inputs),
                    audio_fetch_ms=audio_fetch_ms,
                )
            finally:
                CURRENT_TURN_ID.reset(event_token)

        # The routing decision is not the same as the message's addressee. An accepted
        # bot message is always explicitly invoked; for humans, compare against routing
        # with auto-reply disabled to distinguish a call from passive channel chatter.
        trigger_kind = None
        if text is not None:
            explicit_call = bool(getattr(message.author, "bot", False)) or (
                trigger_text(
                    message,
                    self.user.id,
                    prefixes=self.settings.call_prefixes,
                ) is not None
            )
            trigger_kind = "explicit" if explicit_call else "implicit"

        interaction_token = CURRENT_INTERACTION_CONTEXT.set(
            build_interaction_context(
                message,
                self.user.id,
                replied,
                include_mention_names=not strict_egress,
                trigger_kind=trigger_kind,
            )
        )
        target_token = TARGET_CONTEXT.set(tuple(sampled))
        reply_token = REPLY_CONTEXT.set(tuple(replied))
        channel_context_token = CURRENT_CHANNEL_CONTEXT.set(tuple(selected_context))
        visual_token = CURRENT_VISUAL_INPUTS.set(tuple(visuals))
        unavailable_token = CURRENT_AUDIO_UNAVAILABLE.set(unavailable_audio)
        audio_token = CURRENT_AUDIO_INPUTS.set(tuple(audio_inputs))
        provenance_token = CURRENT_TURN_PROVENANCE.set(
            build_turn_provenance(message, text or "", replied, visuals)
            if text is not None
            else None
        )
        # Keep the user's text untouched. The base client uses request-scoped visual
        # provenance to let current-message / explicit-reply visual-only turns reach the LLM,
        # while a plain bare call still takes the configured fixed-reply path.
        correlation_token = (
            CURRENT_TURN_ID.set(preflight_turn_id)
            if preflight_turn_id is not None
            else None
        )
        registry = LocalToolRegistry()
        confirmed_ids = {str(member.id) for member in getattr(message, "mentions", ())}
        confirmed_ids.update(str(row.get("author_user_id") or row.get("user_id"))
                             for row in (*selected_context, *replied, *sampled))
        channel_member_tool_registry(message, self.user.id, registry=registry, confirmed_ids=confirmed_ids)
        if message.guild and not message.author.bot:
            register_member_relationship(registry, self, message, scope, confirmed_ids)
        register_chat_history_tool(
            registry, message, self.user.id, self.store, self.settings, events=self.events,
        )
        reader = server_reader(self, message, scope) if message.guild and not message.author.bot else None
        reader_token = CURRENT_SERVER_READER.set(reader)
        register_memory_recall_tool(registry, events=self.events)
        local_tools_token = CURRENT_LOCAL_TOOLS.set(registry if registry else None)
        try:
            return await super().on_message(message)
        finally:
            if correlation_token is not None:
                CURRENT_TURN_ID.reset(correlation_token)
            CURRENT_SERVER_READER.reset(reader_token)
            CURRENT_LOCAL_TOOLS.reset(local_tools_token)
            CURRENT_VISUAL_INPUTS.reset(visual_token)
            CURRENT_AUDIO_UNAVAILABLE.reset(unavailable_token)
            CURRENT_AUDIO_INPUTS.reset(audio_token)
            CURRENT_TURN_PROVENANCE.reset(provenance_token)
            CURRENT_CHANNEL_CONTEXT.reset(channel_context_token)
            REPLY_CONTEXT.reset(reply_token)
            TARGET_CONTEXT.reset(target_token)
            CURRENT_INTERACTION_CONTEXT.reset(interaction_token)

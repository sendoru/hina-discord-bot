import asyncio
import logging
import sys
import time
import weakref
from contextlib import ExitStack, asynccontextmanager, nullcontext
from contextvars import ContextVar
from datetime import timedelta

import discord

from hina_bot.ai.vision import CURRENT_VISUAL_INPUTS
from hina_bot.core.config import Settings
from hina_bot.core.emojis import render_emojis
from hina_bot.core.observability import (
    CURRENT_TURN_ID,
    EventLogger,
    current_turn_id,
    new_turn_id,
    safe_exception_fields,
)
from hina_bot.core.recent import RecentMessages
from hina_bot.core.routing import Scope, chunks, trigger_text
from hina_bot.core.store import Store

from .admin_commands import initialize_admin_commands, run_admin_command_once
from .emoji_commands import EmojiRegistry
from .memory_commands import MemoryCommands, MemoryMode
from .output_safety import neutralize_mentions
from .target_recent import CURRENT_CHANNEL_CONTEXT
from .vision import message_has_visual

log = logging.getLogger("hina")

USER_ONLY_ALLOWED_MENTIONS = discord.AllowedMentions(
    everyone=False,
    users=True,
    roles=False,
    replied_user=False,
)
BOT_TRIGGER_CHAIN_LIMIT = 2
BOT_TRIGGER_CHAIN_WINDOW_SECONDS = 15.0
# The production wrapper owns typing across preflight and generation.
CURRENT_TYPING_ACTIVE = ContextVar("current_typing_active", default=False)


@asynccontextmanager
async def _timed_channel_lock(
    channel_lock,
    timings: dict,
    turn_started: float,
    *,
    clock=time.perf_counter,
):
    wait_started = clock()
    await channel_lock.acquire()
    acquired = clock()
    timings["channel_lock_wait_ms"] = round((acquired - wait_started) * 1000)
    timings["lock_wait_ms"] = round((acquired - turn_started) * 1000)
    try:
        yield
    finally:
        channel_lock.release()


@asynccontextmanager
async def _temporarily_released_channel_lock(
    channel_lock,
    timings: dict,
    *,
    clock=time.perf_counter,
):
    channel_lock.release()
    try:
        yield
    finally:
        wait_started = clock()
        await channel_lock.acquire()
        acquired = clock()
        timings["channel_commit_lock_wait_ms"] = round((acquired - wait_started) * 1000)


@asynccontextmanager
async def _timed_memory_lock(
    memory_lock,
    timings: dict,
    *,
    clock=time.perf_counter,
):
    wait_started = clock()
    await memory_lock.acquire()
    acquired = clock()
    timings["memory_lock_wait_ms"] = round((acquired - wait_started) * 1000)
    try:
        yield
    finally:
        memory_lock.release()


def _bare_call_reply(
    scope: Scope,
    special_dm_user_id: int | None,
    empty_call_reply: str,
    special_dm_empty_call_reply: str = "",
) -> str:
    special_dm = (
        scope.guild_id is None
        and special_dm_user_id is not None
        and scope.user_id == special_dm_user_id
    )
    if special_dm and special_dm_empty_call_reply:
        return special_dm_empty_call_reply
    return empty_call_reply


def _has_strong_visual_context(visuals) -> bool:
    """Return whether visuals explicitly belong to the active user turn."""
    return any(
        visual.reference_strength in {"current_message", "explicit_reply"}
        for visual in visuals
    )


class HinaClient(discord.Client):
    def __init__(self, settings: Settings, *, store=None, llm):
        intents = discord.Intents.default()
        intents.members = True
        intents.message_content = True
        intents.emojis_and_stickers = True
        super().__init__(
            intents=intents,
            member_cache_flags=discord.MemberCacheFlags.from_intents(intents),
            chunk_guilds_at_startup=True,
            allowed_mentions=USER_ONLY_ALLOWED_MENTIONS,
            max_messages=None,
        )
        self.settings = settings
        self.store = store or Store(settings.db_path, settings.history_turns)
        self.llm = llm
        self.emoji_registry = EmojiRegistry(self, self.store)
        self.emoji_admin_ids = set(settings.bot_admin_ids)
        self.tree = discord.app_commands.CommandTree(self)
        self.tree.add_command(MemoryCommands(self))
        self.locks = weakref.WeakValueDictionary()
        self.cooldowns = {}
        self.bot_trigger_chains = {}
        self.recent = RecentMessages(budget=settings.channel_context_chars)
        self.channel_locks = weakref.WeakValueDictionary()
        self.slots = asyncio.Semaphore(settings.concurrency)
        self.pending_count = 0
        self.active_tasks = set()
        self.inflight_message_ids = set()
        self.memory_sweep_task = None
        self.admin_command_task = None
        self.stopping = False
        self.events = EventLogger(getattr(settings, "event_log_path", ""))

    def channel_lock(self, scope):
        key = (scope.realm, scope.channel_id)
        lock = self.channel_locks.get(key)
        if lock is None:
            lock = asyncio.Lock()
            self.channel_locks[key] = lock
        return lock

    def memory_lock(self, scope):
        key = scope.user_note
        lock = self.locks.get(key)
        if lock is None:
            lock = asyncio.Lock()
            self.locks[key] = lock
        return lock

    @staticmethod
    def _bot_trigger_key(scope: Scope):
        return scope.guild_id, scope.channel_id

    def _reset_bot_trigger_chain(self, scope: Scope) -> None:
        self.bot_trigger_chains.pop(self._bot_trigger_key(scope), None)

    def _consume_bot_trigger(self, scope: Scope, now: float) -> bool:
        self.bot_trigger_chains = {
            key: value
            for key, value in self.bot_trigger_chains.items()
            if now - value[1] < BOT_TRIGGER_CHAIN_WINDOW_SECONDS
        }
        key = self._bot_trigger_key(scope)
        count, last = self.bot_trigger_chains.get(key, (0, now))
        if now - last >= BOT_TRIGGER_CHAIN_WINDOW_SECONDS:
            count = 0
        if count >= BOT_TRIGGER_CHAIN_LIMIT:
            return False
        self.bot_trigger_chains[key] = (count + 1, now)
        return True

    async def setup_hook(self):
        info = await self.application_info()
        owner_id = info.team.owner_id if info.team else info.owner.id
        self.emoji_admin_ids.add(owner_id)
        await self.emoji_registry.catalog()
        await self.tree.sync()
        start_background_tasks = getattr(self.llm, "start_background_tasks", None)
        if start_background_tasks is not None:
            await start_background_tasks()
        initialize_admin_commands(self)
        if self.admin_command_task is None:
            self.admin_command_task = asyncio.create_task(
                self._admin_command_loop(),
                name="dashboard-admin-commands",
            )
        interval = self.settings.structured_memory_sweep_interval_seconds
        if interval > 0 and self.memory_sweep_task is None:
            self.memory_sweep_task = asyncio.create_task(
                self._memory_sweep_loop(),
                name="structured-memory-sweep",
            )

    async def on_ready(self):
        log.info("Bot connected (id=%s)", self.user.id)

    async def on_error(self, event, *args, **kwargs):
        # Discord's default handler prints message arguments and full tracebacks.
        exc = sys.exception()
        if exc is None:
            log.error("Discord event failed: %s", event)
            return
        error = safe_exception_fields(exc, f"discord_event_{event}")
        self.events.emit(
            "discord.event_failed",
            level="error",
            discord_event=str(event)[:100],
            **error,
        )
        log.error(
            "Discord event failed (%s, event=%s, fingerprint=%s)",
            type(exc).__name__, event, error["error_fingerprint"])

    async def close(self):
        self.stopping = True
        admin_task = self.admin_command_task
        self.admin_command_task = None
        if admin_task is not None:
            admin_task.cancel()
            await asyncio.gather(admin_task, return_exceptions=True)
        sweep_task = self.memory_sweep_task
        self.memory_sweep_task = None
        if sweep_task is not None:
            sweep_task.cancel()
            await asyncio.gather(sweep_task, return_exceptions=True)
        try:
            if self.active_tasks:
                _, pending = await asyncio.wait(list(self.active_tasks), timeout=50)
                for task in pending:
                    task.cancel()
                await asyncio.gather(*pending, return_exceptions=True)
        finally:
            await self.llm.close()
            self.store.close()
            self.events.close()
            await super().close()

    async def _admin_command_loop(self):
        while not self.stopping:
            try:
                processed = 0
                while processed < 10 and await run_admin_command_once(self):
                    processed += 1
                await asyncio.sleep(0.25 if processed else 1.0)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - keep admin polling isolated
                log.warning("Dashboard admin command poll failed (%s)", type(exc).__name__)
                await asyncio.sleep(1.0)

    async def _memory_sweep_loop(self):
        interval = self.settings.structured_memory_sweep_interval_seconds
        while not self.stopping:
            await asyncio.sleep(interval)
            if self.stopping:
                return
            try:
                await self._sweep_stale_structured_memory()
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - isolate background maintenance failures
                error = safe_exception_fields(exc, "memory_stale_sweep")
                self.events.emit(
                    "memory.stale_sweep_failed",
                    level="warning",
                    **error,
                )
                log.warning(
                    "Structured memory stale sweep failed (%s, fingerprint=%s)",
                    type(exc).__name__,
                    error["error_fingerprint"],
                )

    async def _sweep_stale_structured_memory(self):
        scopes = self.store.stale_memory_extraction_scopes(
            min_pending=2,
            stale_after_seconds=self.settings.structured_memory_stale_after_seconds,
        )
        for scope in scopes:
            if self.stopping:
                return
            memory_lock = self.memory_lock(scope)
            try:
                async with memory_lock:
                    mode = MemoryMode(self.store.memory_mode(scope))
                    if not mode.writes:
                        continue
                    async with self.slots:
                        committed = await self.llm.extract_structured_memory(
                            self.store,
                            scope,
                            min_turns=2,
                        )
                if committed:
                    self.events.emit(
                        "memory.stale_sweep_completed",
                        scope="guild" if scope.guild_id is not None else "dm",
                    )
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - isolate one stale scope from the rest
                error = safe_exception_fields(exc, "memory_stale_scope")
                self.events.emit(
                    "memory.extraction_failed",
                    level="warning",
                    scope="guild" if scope.guild_id is not None else "dm",
                    memory_kind="structured_stale",
                    **error,
                )
                log.warning(
                    "Stale structured memory update deferred (%s, fingerprint=%s)",
                    type(exc).__name__,
                    error["error_fingerprint"],
                )

    async def send_text(self, channel, text):
        for part in chunks(neutralize_mentions(text)):
            await channel.send(part, allowed_mentions=USER_ONLY_ALLOWED_MENTIONS)

    async def hydrate_recent_history(self, message, scope):
        """Backfill only the bounded recent window after restart or chat-log re-enable."""
        if scope.guild_id is None or not self.recent.needs_hydration(scope):
            return
        created_at = getattr(message, "created_at", None)
        history = getattr(message.channel, "history", None)
        if created_at is None or history is None:
            # Unit-test/minimal adapters may not expose Discord history. Avoid retry loops there.
            self.recent.mark_hydrated(scope)
            return

        after = created_at - timedelta(seconds=self.recent.ttl)
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
                if old.author.bot and not own_bot:
                    continue
                has_visual = message_has_visual(old)
                if not old.content and not has_visual:
                    continue
                historical_scope = Scope(
                    scope.guild_id, scope.channel_id, old.author.id, scope.public_at_capture)
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
                self.recent.add(
                    historical_scope,
                    old.id,
                    old.author.display_name,
                    old.content,
                    role="assistant" if own_bot else "user",
                    unix_time=old.created_at.timestamp(),
                    author_user_id=old.author.id,
                    reply_target_user_id=None,
                    direct_trigger=None if own_bot else historical_text is not None,
                    has_visual=has_visual,
                )
        except discord.HTTPException as exc:
            # Do not log message contents or channel data. Retry naturally on a later call.
            log.warning("Recent channel history backfill failed (%s)", type(exc).__name__)
            return
        self.recent.mark_hydrated(scope)

    async def public_sources(self, user_id: int, guild_id: int | None = None):
        """Fail closed; only ordinary public text channels, with live membership checks."""
        if guild_id is None and not self.settings.public_memory_in_dm:
            return []
        allowed, members = [], {}
        for scope in self.store.public_candidates(user_id, guild_id):
            if (self.settings.allowed_guild_ids
                    and scope.guild_id not in self.settings.allowed_guild_ids):
                continue
            guild = self.get_guild(scope.guild_id)
            if guild is None or guild.unavailable:
                continue
            # Exclude all threads, voice chats and forum posts in v1, even public ones.
            channel = guild.get_channel(scope.channel_id)
            if not isinstance(channel, discord.TextChannel):
                continue
            public = channel.permissions_for(guild.default_role)
            if not (public.view_channel and public.read_message_history):
                continue
            if scope.guild_id not in members:
                try:
                    members[scope.guild_id] = await guild.fetch_member(user_id)
                except discord.HTTPException:
                    members[scope.guild_id] = None
            member = members[scope.guild_id]
            if member is None:
                continue
            permissions = channel.permissions_for(member)
            if not (permissions.view_channel and permissions.read_message_history):
                continue
            allowed.append(scope)
            if len(allowed) == 4:
                break
        return allowed

    async def on_message(self, message):
        if self.stopping or not self.user:
            return
        guild_id = message.guild.id if message.guild else None
        if (guild_id is not None and self.settings.allowed_guild_ids
                and guild_id not in self.settings.allowed_guild_ids):
            return
        text = trigger_text(
            message,
            self.user.id,
            self.settings.dm_always_reply,
            self.settings.call_prefixes,
            self.settings.always_reply_channel_ids,
        )
        public_at_capture = False
        if guild_id is not None and isinstance(message.channel, discord.TextChannel):
            permissions = message.channel.permissions_for(message.guild.default_role)
            public_at_capture = permissions.view_channel and permissions.read_message_history
        scope = Scope(guild_id, message.channel.id, message.author.id, public_at_capture)
        bot_author = bool(message.author.bot)
        if message.webhook_id is not None or message.author.id == self.user.id:
            return
        if not bot_author:
            # Any human activity breaks a possible bot-to-bot response chain in this channel.
            self._reset_bot_trigger_chain(scope)
        received_mode = (
            MemoryMode.off if bot_author else MemoryMode(self.store.memory_mode(scope))
        )
        received_chat_log = self.store.chat_log_enabled(scope)
        # Recent chat context is independent from persistent memory and has its own switch.
        if guild_id is not None and received_chat_log:
            created_at = getattr(message, "created_at", None)
            self.recent.add(
                scope,
                message.id,
                message.author.display_name,
                message.content,
                role="bot" if bot_author else "user",
                unix_time=created_at.timestamp() if created_at is not None else None,
                author_user_id=message.author.id,
                reply_target_user_id=None,
                direct_trigger=text is not None,
                has_visual=message_has_visual(message),
            )
        if text is None:
            return
        turn_token = CURRENT_TURN_ID.set(current_turn_id() or new_turn_id())
        turn_started = time.perf_counter()
        cooldown_received_at = time.monotonic()
        scope_kind = "guild" if guild_id is not None else "dm"
        self.events.emit(
            "turn.received",
            scope=scope_kind,
            pending_count=self.pending_count,
            content_chars=len(text),
            attachment_count=len(getattr(message, "attachments", ()) or ()),
            author_kind="bot" if bot_author else "user",
        )
        if bot_author and not self._consume_bot_trigger(scope, turn_started):
            self.events.emit(
                "turn.dropped",
                scope=scope_kind,
                reason="bot_loop_guard",
                elapsed_ms=round((time.perf_counter() - turn_started) * 1000),
            )
            CURRENT_TURN_ID.reset(turn_token)
            return
        if self.pending_count >= 100:
            self.events.emit(
                "turn.dropped",
                level="warning",
                scope=scope_kind,
                reason="queue_full",
                pending_count=self.pending_count,
                elapsed_ms=round((time.perf_counter() - turn_started) * 1000),
            )
            CURRENT_TURN_ID.reset(turn_token)
            return
        channel_lock = self.channel_lock(scope)
        # Persistent-memory extraction remains serialized per realm+user, but it no longer
        # holds the channel lock after the visible reply has been delivered.
        key = scope.user_note
        memory_lock = self.memory_lock(scope)
        self.pending_count += 1
        task = asyncio.current_task()
        self.active_tasks.add(task)
        stage = "lock_wait"
        reply_delivered = False
        terminal_emitted = False
        save_memory = False
        raw_turn_persistence = "pending"
        raw_turn_persistence_reason = ""
        answer = ""
        timings = {}
        inflight_registered = False

        def raw_turn_persistence_fields() -> dict[str, str]:
            if raw_turn_persistence == "pending":
                return {}
            fields = {"raw_turn_persistence": raw_turn_persistence}
            if raw_turn_persistence_reason:
                fields["raw_turn_persistence_reason"] = raw_turn_persistence_reason
            return fields

        def persist_failed_turn(status: str, failed_stage: str, error_fields: dict, reply: str):
            nonlocal raw_turn_persistence, raw_turn_persistence_reason
            if not save_memory:
                raw_turn_persistence = "skipped"
                raw_turn_persistence_reason = "memory_writes_disabled"
                return
            if self.store.seen(message.id):
                raw_turn_persistence = "stored"
                raw_turn_persistence_reason = ""
                return
            try:
                self.store.add_failed_turn(
                    scope,
                    message.id,
                    text,
                    name=message.author.display_name,
                    reply=reply,
                    status=status,
                    stage=failed_stage,
                    reply_delivered=reply_delivered,
                    error_type=str(error_fields.get("error_type") or ""),
                    error_fingerprint=str(error_fields.get("error_fingerprint") or ""),
                )
                raw_turn_persistence = "stored"
                raw_turn_persistence_reason = ""
            except Exception as persist_exc:  # noqa: BLE001 - preserve the original failure
                raw_turn_persistence = "failed"
                raw_turn_persistence_reason = "persistence_error"
                persist_error = safe_exception_fields(persist_exc, "failed_turn_persist")
                self.events.emit(
                    "turn.persistence_failed",
                    level="warning",
                    scope=scope_kind,
                    target="failed_turn",
                    **persist_error,
                )
                log.warning(
                    "Failed-turn persistence failed (%s, turn_id=%s, fingerprint=%s)",
                    type(persist_exc).__name__,
                    current_turn_id(),
                    persist_error["error_fingerprint"],
                )

        try:
            with ExitStack() as exchange_stack:
                async with _timed_channel_lock(
                    channel_lock,
                    timings,
                    turn_started,
                ):
                    mode = (
                        MemoryMode.off if bot_author else MemoryMode(self.store.memory_mode(scope))
                    )
                    use_memory = received_mode.reads and mode.reads
                    save_memory = received_mode.writes and mode.writes
                    if not save_memory:
                        raw_turn_persistence = "skipped"
                        raw_turn_persistence_reason = "memory_writes_disabled"
                    use_chat_log = received_chat_log and self.store.chat_log_enabled(scope)
                    if (
                        self.store.seen(message.id)
                        or message.id in self.inflight_message_ids
                    ):
                        self.events.emit(
                            "turn.dropped",
                            scope=scope_kind,
                            reason="duplicate",
                            elapsed_ms=round((time.perf_counter() - turn_started) * 1000),
                        )
                        terminal_emitted = True
                        return
                    self.inflight_message_ids.add(message.id)
                    inflight_registered = True
                    if len(text) > 4000:
                        stage = "delivery"
                        await self.send_text(
                            message.channel,
                            "한 번에 4000자 이내로 이야기해 주세요.",
                        )
                        reply_delivered = True
                        self.events.emit(
                            "turn.dropped",
                            scope=scope_kind,
                            reason="input_too_long",
                            reply_delivered=True,
                            elapsed_ms=round((time.perf_counter() - turn_started) * 1000),
                        )
                        terminal_emitted = True
                        return

                    # Cooldown is based on when Discord delivered the turn to this process,
                    # not on when an earlier channel-lock backlog finally cleared.
                    self.cooldowns = {
                        cooldown_key: accepted_at
                        for cooldown_key, accepted_at in self.cooldowns.items()
                        if cooldown_received_at - accepted_at < self.settings.cooldown
                    }
                    if key in self.cooldowns:
                        self.events.emit(
                            "turn.dropped",
                            scope=scope_kind,
                            reason="cooldown",
                            elapsed_ms=round((time.perf_counter() - turn_started) * 1000),
                        )
                        terminal_emitted = True
                        return
                    self.cooldowns[key] = cooldown_received_at

                    if not text and not _has_strong_visual_context(CURRENT_VISUAL_INPUTS.get()):
                        raw_turn_persistence = "skipped"
                        raw_turn_persistence_reason = "fixed_reply_no_raw_turn"
                        stage = "delivery"
                        delivery_started = time.perf_counter()
                        await self.send_text(
                            message.channel,
                            _bare_call_reply(
                                scope,
                                self.settings.special_dm_user_id,
                                self.settings.empty_call_reply,
                                self.settings.special_dm_empty_call_reply,
                            ),
                        )
                        reply_delivered = True
                        timings["delivery_ms"] = round(
                            (time.perf_counter() - delivery_started) * 1000
                        )
                        self.events.emit(
                            "turn.reply_delivered",
                            scope=scope_kind,
                            elapsed_ms=round((time.perf_counter() - turn_started) * 1000),
                            delivery_ms=timings["delivery_ms"],
                            delivery_chunks=1,
                        )
                        self.events.emit(
                            "turn.completed",
                            scope=scope_kind,
                            status="completed",
                            reply_delivered=True,
                            elapsed_ms=round((time.perf_counter() - turn_started) * 1000),
                            **timings,
                            **raw_turn_persistence_fields(),
                        )
                        terminal_emitted = True
                        return

                    if guild_id is not None and use_chat_log:
                        stage = "recent_history"
                        recent_history_started = time.perf_counter()
                        await self.hydrate_recent_history(message, scope)
                        timings["recent_history_ms"] = round(
                            (time.perf_counter() - recent_history_started) * 1000
                        )

                    usage = getattr(self.llm, "usage", None)
                    exchange = (
                        usage.exchange("guild" if guild_id is not None else "dm")
                        if usage is not None and hasattr(usage, "exchange")
                        else nullcontext()
                    )
                    exchange_stack.enter_context(exchange)

                    async with _temporarily_released_channel_lock(
                        channel_lock,
                        timings,
                    ):
                        slot_started = time.perf_counter()
                        async with self.slots:
                            timings["slot_wait_ms"] = round(
                                (time.perf_counter() - slot_started) * 1000
                            )
                            async with (
                                nullcontext()
                                if CURRENT_TYPING_ACTIVE.get()
                                else message.channel.typing()
                            ):
                                stage = "context"
                                context_started = time.perf_counter()
                                sources = (
                                    await self.public_sources(scope.user_id, guild_id)
                                    if use_memory
                                    else []
                                )
                                context = self.store.public_context(sources) if use_memory else []
                                emoji_catalog = await self.emoji_registry.catalog(message.channel)
                                timings["context_ms"] = round(
                                    (time.perf_counter() - context_started) * 1000
                                )

                                stage = "generation"
                                generation_started = time.perf_counter()
                                answer = await self.llm.answer(
                                    self.store,
                                    scope,
                                    message.author.display_name,
                                    text,
                                    public_context=context,
                                    channel_context=(
                                        (
                                            list(CURRENT_CHANNEL_CONTEXT.get())
                                            if CURRENT_CHANNEL_CONTEXT.get() is not None
                                            else self.recent.context(scope, message.id)
                                        )
                                        if guild_id is not None and use_chat_log
                                        else []
                                    ),
                                    use_memory=use_memory,
                                    emoji_catalog=emoji_catalog,
                                )
                                timings["generation_ms"] = round(
                                    (time.perf_counter() - generation_started) * 1000
                                )
                                current = {
                                    e["id"]
                                    for e in await self.emoji_registry.catalog(message.channel)
                                }
                                answer = render_emojis(
                                    answer,
                                    [e for e in emoji_catalog if e["id"] in current],
                                )
                                answer = neutralize_mentions(answer)
                                if not answer:
                                    answer = self.settings.empty_response_reply
                                parts = list(chunks(answer))

                                stage = "delivery"
                                delivery_started = time.perf_counter()
                                sent = await message.channel.send(
                                    parts[0],
                                    allowed_mentions=USER_ONLY_ALLOWED_MENTIONS,
                                )
                                for part in parts[1:]:
                                    await message.channel.send(
                                        part,
                                        allowed_mentions=USER_ONLY_ALLOWED_MENTIONS,
                                    )
                                reply_delivered = True
                                timings["delivery_ms"] = round(
                                    (time.perf_counter() - delivery_started) * 1000
                                )
                                self.events.emit(
                                    "turn.reply_delivered",
                                    scope=scope_kind,
                                    elapsed_ms=round(
                                        (time.perf_counter() - turn_started) * 1000
                                    ),
                                    delivery_ms=timings["delivery_ms"],
                                    delivery_chunks=len(parts),
                                )
                                if guild_id is not None and use_chat_log:
                                    assistant_name = getattr(
                                        self.user,
                                        "display_name",
                                        "assistant",
                                    )[:100]
                                    sent_at = getattr(sent, "created_at", None)
                                    self.recent.add(
                                        scope,
                                        sent.id,
                                        assistant_name,
                                        answer,
                                        role="assistant",
                                        unix_time=(
                                            sent_at.timestamp()
                                            if sent_at is not None
                                            else None
                                        ),
                                        author_user_id=self.user.id,
                                        reply_target_user_id=scope.user_id,
                                        direct_trigger=None,
                                        capture_turn_provenance=True,
                                    )

                    # Reacquire the channel lock before committing the completed turn. Later turns
                    # may already be generating from their own arrival-time context snapshots.
                    # Expensive persistent-memory updates run after the channel lock is released.
                    if save_memory:
                        stage = "memory_persist"
                        self.store.add(
                            scope,
                            message.id,
                            text,
                            answer,
                            name=message.author.display_name,
                        )
                        raw_turn_persistence = "stored"
                        raw_turn_persistence_reason = ""
                        self.store.add_shared_call(
                            scope,
                            message.id,
                            message.author.display_name,
                            text,
                        )

                memory_failures = 0
                if save_memory:
                    stage = "memory"
                    memory_started = time.perf_counter()
                    async with _timed_memory_lock(memory_lock, timings):
                        for memory_kind, update, failure_event in (
                            (
                                "structured",
                                self.llm.extract_structured_memory,
                                "memory.extraction_failed",
                            ),
                            ("shared", self.llm.summarize_shared, "memory.summary_failed"),
                        ):
                            try:
                                await update(self.store, scope)
                            except Exception as exc:  # noqa: BLE001 - isolate memory failures; redact logs
                                memory_failures += 1
                                error = safe_exception_fields(
                                    exc,
                                    f"memory_{memory_kind}",
                                )
                                self.events.emit(
                                    failure_event,
                                    level="warning",
                                    scope=scope_kind,
                                    memory_kind=memory_kind,
                                    **error,
                                )
                                log.warning(
                                    "Memory update deferred (%s, kind=%s, turn_id=%s, fingerprint=%s)",
                                    type(exc).__name__,
                                    memory_kind,
                                    current_turn_id(),
                                    error["error_fingerprint"],
                                )
                    timings["memory_ms"] = round(
                        (time.perf_counter() - memory_started) * 1000
                    )

                self.events.emit(
                    "turn.completed",
                    scope=scope_kind,
                    status="partial_success" if memory_failures else "completed",
                    reply_delivered=reply_delivered,
                    memory_failures=memory_failures,
                    answer_chars=len(answer),
                    delivery_chunks=len(parts),
                    elapsed_ms=round((time.perf_counter() - turn_started) * 1000),
                    **timings,
                    **raw_turn_persistence_fields(),
                )
                terminal_emitted = True
        except asyncio.CancelledError as exc:
            error = safe_exception_fields(exc, stage)
            self.events.emit(
                "turn.failed",
                level="warning",
                scope=scope_kind,
                status="cancelled",
                stage=stage,
                reply_delivered=reply_delivered,
                elapsed_ms=round((time.perf_counter() - turn_started) * 1000),
                **raw_turn_persistence_fields(),
                **error,
            )
            terminal_emitted = True
            raise
        except discord.HTTPException as exc:
            failed_stage = stage
            error = safe_exception_fields(exc, failed_stage)
            status = "delivery_failed" if failed_stage == "delivery" else "failed"
            persist_failed_turn(status, failed_stage, error, answer if reply_delivered else "")
            self.events.emit(
                "turn.failed",
                level="error",
                scope=scope_kind,
                status=status,
                stage=failed_stage,
                reply_delivered=reply_delivered,
                elapsed_ms=round((time.perf_counter() - turn_started) * 1000),
                **raw_turn_persistence_fields(),
                **error,
            )
            terminal_emitted = True
            log.warning(
                "Discord request failed (%s, stage=%s, turn_id=%s, fingerprint=%s)",
                type(exc).__name__,
                failed_stage,
                current_turn_id(),
                error["error_fingerprint"],
            )
        except Exception as exc:  # noqa: BLE001 - isolate event/summary failures; redact logs
            failed_stage = stage
            error = safe_exception_fields(exc, failed_stage)
            status = "generation_failed" if failed_stage == "generation" else "failed"
            log.warning(
                "Conversation failed (%s, stage=%s, turn_id=%s, fingerprint=%s)",
                type(exc).__name__,
                failed_stage,
                current_turn_id(),
                error["error_fingerprint"],
            )

            fallback_reply = ""
            if not reply_delivered:
                fallback_reply = "지금은 답변을 이어가기 어렵네요. 잠시 후 다시 불러 주세요."
                fallback_started = time.perf_counter()
                try:
                    await self.send_text(message.channel, fallback_reply)
                    reply_delivered = True
                    timings["fallback_delivery_ms"] = round(
                        (time.perf_counter() - fallback_started) * 1000
                    )
                    self.events.emit(
                        "turn.reply_delivered",
                        scope=scope_kind,
                        fallback=True,
                        elapsed_ms=round((time.perf_counter() - turn_started) * 1000),
                        delivery_ms=timings["fallback_delivery_ms"],
                        delivery_chunks=1,
                    )
                except Exception as fallback_exc:  # noqa: BLE001 - preserve the original failure
                    fallback_error = safe_exception_fields(fallback_exc, "fallback_delivery")
                    log.warning(
                        "Fallback delivery failed (%s, turn_id=%s, fingerprint=%s)",
                        type(fallback_exc).__name__,
                        current_turn_id(),
                        fallback_error["error_fingerprint"],
                    )
                    fallback_reply = ""

            persisted_reply = (
                fallback_reply
                if fallback_reply
                else answer if answer and reply_delivered else ""
            )
            persist_failed_turn(status, failed_stage, error, persisted_reply)
            self.events.emit(
                "turn.failed",
                level="error",
                scope=scope_kind,
                status=status,
                stage=failed_stage,
                reply_delivered=reply_delivered,
                elapsed_ms=round((time.perf_counter() - turn_started) * 1000),
                **timings,
                **raw_turn_persistence_fields(),
                **error,
            )
            terminal_emitted = True
        finally:
            if not terminal_emitted:
                self.events.emit(
                    "turn.failed",
                    level="error",
                    scope=scope_kind,
                    status="aborted",
                    stage=stage,
                    reply_delivered=reply_delivered,
                    elapsed_ms=round((time.perf_counter() - turn_started) * 1000),
                    **raw_turn_persistence_fields(),
                )
            if inflight_registered:
                self.inflight_message_ids.discard(message.id)
            self.active_tasks.discard(task)
            self.pending_count -= 1
            CURRENT_TURN_ID.reset(turn_token)

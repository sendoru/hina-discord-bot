import asyncio
import logging
import time
import weakref
from contextlib import nullcontext
from datetime import timedelta

import discord

from hina_bot.ai.information_pipeline import LLM
from hina_bot.core.config import Settings
from hina_bot.core.emojis import render_emojis
from hina_bot.core.recent import RecentMessages
from hina_bot.core.routing import Scope, chunks, trigger_text
from hina_bot.core.store import Store

from .emoji_commands import EmojiRegistry
from .memory_commands import MemoryCommands, MemoryMode
from .output_safety import neutralize_mentions

log = logging.getLogger("hina")

def _bare_call_reply(scope: Scope, special_dm_user_id: int | None) -> str:
    special_dm = (
        scope.guild_id is None
        and special_dm_user_id is not None
        and scope.user_id == special_dm_user_id
    )
    return "응, 선생님. 무슨 일이야?" if special_dm else "응? 무슨 일이야?"


class HinaClient(discord.Client):
    def __init__(self, settings: Settings, *, store=None, llm=None):
        intents = discord.Intents.default()
        intents.message_content = True
        intents.emojis_and_stickers = True
        super().__init__(intents=intents, allowed_mentions=discord.AllowedMentions.none(),
                         max_messages=None)
        self.settings = settings
        self.store = store or Store(settings.db_path, settings.history_turns)
        self.llm = llm or LLM(settings)
        self.emoji_registry = EmojiRegistry(self, self.store)
        self.emoji_admin_ids = set(settings.bot_admin_ids)
        self.tree = discord.app_commands.CommandTree(self)
        self.tree.add_command(MemoryCommands(self))
        self.locks = weakref.WeakValueDictionary()
        self.cooldowns = {}
        self.recent = RecentMessages(budget=settings.channel_context_chars)
        self.channel_locks = weakref.WeakValueDictionary()
        self.slots = asyncio.Semaphore(settings.concurrency)
        self.pending_count = 0
        self.active_tasks = set()
        self.stopping = False

    def channel_lock(self, scope):
        key = (scope.realm, scope.channel_id)
        lock = self.channel_locks.get(key)
        if lock is None:
            lock = asyncio.Lock()
            self.channel_locks[key] = lock
        return lock

    async def setup_hook(self):
        info = await self.application_info()
        owner_id = info.team.owner_id if info.team else info.owner.id
        self.emoji_admin_ids.add(owner_id)
        await self.emoji_registry.catalog()
        await self.tree.sync()

    async def on_ready(self):
        log.info("Bot connected (id=%s)", self.user.id)

    async def on_error(self, event, *args, **kwargs):
        # Discord's default handler prints message arguments and full tracebacks.
        log.error("Discord event failed: %s", event)

    async def close(self):
        self.stopping = True
        try:
            if self.active_tasks:
                _, pending = await asyncio.wait(list(self.active_tasks), timeout=50)
                for task in pending:
                    task.cancel()
                await asyncio.gather(*pending, return_exceptions=True)
        finally:
            await self.llm.close()
            self.store.close()
            await super().close()

    async def send_text(self, channel, text):
        for part in chunks(neutralize_mentions(text)):
            await channel.send(part, allowed_mentions=discord.AllowedMentions.none())

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
                if not old.content:
                    continue
                historical_scope = Scope(
                    scope.guild_id, scope.channel_id, old.author.id, scope.public_at_capture)
                self.recent.add(
                    historical_scope,
                    old.id,
                    old.author.display_name,
                    old.content,
                    role="assistant" if own_bot else "user",
                    unix_time=old.created_at.timestamp(),
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
        text = trigger_text(message, self.user.id, self.settings.dm_always_reply,
                            self.settings.call_prefixes)
        if self.pending_count >= 100:
            return
        public_at_capture = False
        if guild_id is not None and isinstance(message.channel, discord.TextChannel):
            permissions = message.channel.permissions_for(message.guild.default_role)
            public_at_capture = permissions.view_channel and permissions.read_message_history
        scope = Scope(guild_id, message.channel.id, message.author.id, public_at_capture)
        if message.author.bot or message.webhook_id is not None:
            return
        received_mode = MemoryMode(self.store.memory_mode(scope))
        received_chat_log = self.store.chat_log_enabled(scope)
        # Recent chat context is independent from persistent memory and has its own switch.
        if guild_id is not None and received_chat_log:
            self.recent.add(scope, message.id, message.author.display_name, message.content)
        if text is None:
            return
        channel_lock = self.channel_lock(scope)
        # One lock per realm+user serializes persistent-memory updates across channels.
        key = scope.user_note
        lock = self.locks.get(key)
        if lock is None:
            lock = asyncio.Lock()
            self.locks[key] = lock
        self.pending_count += 1
        task = asyncio.current_task()
        self.active_tasks.add(task)
        try:
            async with channel_lock, lock:
                mode = MemoryMode(self.store.memory_mode(scope))
                use_memory = received_mode.reads and mode.reads
                save_memory = received_mode.writes and mode.writes
                use_chat_log = received_chat_log and self.store.chat_log_enabled(scope)
                if self.store.seen(message.id):
                    return
                if len(text) > 4000:
                    await self.send_text(message.channel, "한 번에 4000자 이내로 이야기해 주세요.")
                    return
                now = time.monotonic()
                self.cooldowns = {k: v for k, v in self.cooldowns.items()
                                  if now - v < self.settings.cooldown}
                if key in self.cooldowns:
                    return
                self.cooldowns[key] = now
                if not text:
                    await self.send_text(
                        message.channel,
                        _bare_call_reply(scope, self.settings.special_dm_user_id),
                    )
                    return
                if guild_id is not None and use_chat_log:
                    await self.hydrate_recent_history(message, scope)
                usage = getattr(self.llm, "usage", None)
                exchange = (usage.exchange("guild" if guild_id is not None else "dm")
                            if usage is not None and hasattr(usage, "exchange") else nullcontext())
                with exchange:
                    async with self.slots:
                        async with message.channel.typing():
                            sources = await self.public_sources(scope.user_id, guild_id) if use_memory else []
                            context = self.store.public_context(sources) if use_memory else []
                            emoji_catalog = await self.emoji_registry.catalog(message.channel)
                            answer = await self.llm.answer(
                                self.store, scope, message.author.display_name, text,
                                public_context=context,
                                channel_context=(self.recent.context(scope, message.id)
                                                 if guild_id is not None and use_chat_log else []),
                                use_memory=use_memory,
                                emoji_catalog=emoji_catalog)
                            current = {e["id"] for e in await self.emoji_registry.catalog(message.channel)}
                            answer = render_emojis(answer, [e for e in emoji_catalog if e["id"] in current])
                            answer = neutralize_mentions(answer)
                            if not answer:
                                answer = "응, 선생님."
                            sent = await message.channel.send(
                                next(chunks(answer)), allowed_mentions=discord.AllowedMentions.none())
                            for part in list(chunks(answer))[1:]:
                                await message.channel.send(part, allowed_mentions=discord.AllowedMentions.none())
                            if guild_id is not None and use_chat_log:
                                self.recent.add(scope, sent.id, "히나", answer, role="assistant")
                        # Commit only after Discord delivery. Never memorize a failed model request.
                        if save_memory:
                            self.store.add(scope, message.id, text, answer)
                            self.store.add_shared_call(scope, message.id, message.author.display_name, text)
                            for summarize in (self.llm.summarize, self.llm.summarize_shared):
                                try:
                                    await summarize(self.store, scope)
                                except Exception as exc:  # noqa: BLE001 - isolate summary failures; redact logs
                                    log.warning("Memory summary deferred (%s)", type(exc).__name__)
        except discord.HTTPException as exc:
            log.warning("Discord delivery failed (%s)", type(exc).__name__)
        except Exception as exc:  # noqa: BLE001 - isolate event/summary failures; redact logs
            log.warning("Conversation failed (%s)", type(exc).__name__)
            try:
                await self.send_text(message.channel, "지금은 답변을 이어가기 어렵네요. 잠시 후 다시 불러 주세요.")
            except discord.HTTPException:
                pass
        finally:
            self.active_tasks.discard(task)
            self.pending_count -= 1


def main():
    # Do not log SDK request bodies, prompts, credentials, or Discord message content.
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

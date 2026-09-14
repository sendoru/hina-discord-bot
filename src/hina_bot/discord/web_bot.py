import logging
import re
from contextvars import ContextVar
from datetime import timedelta

import discord

from hina_bot.ai.vision import CURRENT_VISUAL_INPUTS

from .bot import HinaClient as BaseHinaClient
from .chat_llm import LLM
from .chatlog_capture import capture_mode
from .chatlog_capture_commands import install_chatlog_capture
from .config import Settings
from .reply_context import REPLY_CONTEXT, collect_reply_context
from .routing import Scope, trigger_text
from .slash_commands import install_slash_commands
from .target_context import TARGET_CONTEXT, collect
from .target_recent import CURRENT_DIRECT_TRIGGER, TargetAwareRecentMessages
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
_BROAD_SERVER_MEMORY_QUERY = re.compile(
    r"(?:서버(?:에서|의).*(?:누가|누구|사람들|다른\s*사람)|"
    r"누가.*(?:말했|얘기했))",
    re.IGNORECASE,
)


def _augment_empty_call(content: str, text: str | None, has_visuals: bool) -> str | None:
    """Only add intent when a bare trigger actually carries a visual attachment."""
    if text is None or text or not has_visuals:
        return None
    return (content + " 이 이미지나 스티커를 봐줘.").strip()


def _public_context_request(scope: Scope, text: str, sampled: list[dict]):
    """Choose whether cross-channel public memory is relevant to this invocation.

    Ordinary chat should not receive unrelated users' summaries. Explicitly targeted user questions
    may use that target's public memory, while history/memory questions without a target default to
    the current user's own public calls. Only clearly broad server-history questions may fan out.
    """
    target_ids = tuple({
        int(item["user_id"])
        for item in sampled
        if str(item.get("user_id", "")).isdigit()
    })
    if target_ids:
        return True, target_ids
    if not _PUBLIC_MEMORY_QUERY.search(text):
        return False, ()
    if scope.guild_id is not None and _BROAD_SERVER_MEMORY_QUERY.search(text):
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
        )
        self.vision_limits = VisionLimits.from_settings(settings)
        install_slash_commands(self)
        install_chatlog_capture(self)

    @staticmethod
    def _management_text(text):
        return False

    async def command(self, message, scope, text):
        return None

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
        """Backfill the bounded history while respecting the active capture policy."""
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
                # Discord history does not reliably identify who an old Hina message answered.
                # Such rows would fail closed later anyway and can evict useful user messages from
                # the bounded buffer, so do not hydrate them at all.
                if own_bot:
                    continue
                other_bot = bool(old.author.bot)
                historical_text = trigger_text(
                    old,
                    self.user.id,
                    self.settings.dm_always_reply,
                    self.settings.call_prefixes,
                )
                if policy == "direct" and historical_text is None:
                    continue
                if self._management_text(historical_text) or not old.content:
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
                        role="bot" if other_bot else "user",
                        unix_time=old.created_at.timestamp(),
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

        # Other bots never trigger Hina, but in `capture=all` their visible channel messages are
        # useful conversational context just like human side chatter. `capture=direct` keeps its
        # stricter privacy/attention boundary and omits them unless the current user explicitly
        # replies to one, which is handled below as request-scoped reply context.
        own_bot = message.author.id == self.user.id
        if message.author.bot and not own_bot:
            if (
                message.webhook_id is None
                and scope.guild_id is not None
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

        direct_only = scope.guild_id is not None and capture_mode(self.store, scope) == "direct"
        sampled = (
            await collect(
                message,
                self.user.id,
                text,
                direct_only=direct_only,
                call_prefixes=self.settings.call_prefixes,
            )
            if text is not None
            else []
        )
        replied = (
            await collect_reply_context(message, self.user.id)
            if text is not None
            else []
        )
        visuals = (
            await collect_visual_inputs(message, limits=self.vision_limits)
            if text is not None else []
        )
        public_request = (
            _public_context_request(scope, text, sampled)
            if text is not None
            else (False, ())
        )
        target_token = TARGET_CONTEXT.set(tuple(sampled))
        reply_token = REPLY_CONTEXT.set(tuple(replied))
        visual_token = CURRENT_VISUAL_INPUTS.set(tuple(visuals))
        public_token = CURRENT_PUBLIC_CONTEXT_REQUEST.set(public_request)
        direct_token = CURRENT_DIRECT_TRIGGER.set(text is not None)

        # A text-only bare call stays a bare call and uses the base client's relationship-aware
        # fixed reply. Only a visual-only call gets an explicit visual request so vision reaches
        # the LLM without inventing an unrelated user intent such as "look at something".
        original_content = None
        augmented_content = _augment_empty_call(message.content, text, bool(visuals))
        if augmented_content is not None:
            original_content = message.content
            message.content = augmented_content
        try:
            return await super().on_message(message)
        finally:
            if original_content is not None:
                message.content = original_content
            CURRENT_DIRECT_TRIGGER.reset(direct_token)
            CURRENT_PUBLIC_CONTEXT_REQUEST.reset(public_token)
            CURRENT_VISUAL_INPUTS.reset(visual_token)
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

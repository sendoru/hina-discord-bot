import logging
from datetime import timedelta

import discord

from hina_bot.ai.vision import CURRENT_VISUAL_INPUTS

from .bot import HinaClient as BaseHinaClient
from .chat_llm import LLM
from .chatlog_capture import capture_mode
from .chatlog_capture_commands import install_chatlog_capture
from .config import Settings
from .routing import Scope, trigger_text
from .slash_commands import install_slash_commands
from .target_context import TARGET_CONTEXT, collect
from .target_recent import CURRENT_DIRECT_TRIGGER, TargetAwareRecentMessages
from .vision import VisionLimits, collect_visual_inputs

log = logging.getLogger("hina")


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
                if old.author.bot and not own_bot:
                    continue
                historical_text = trigger_text(
                    old,
                    self.user.id,
                    self.settings.dm_always_reply,
                    self.settings.call_prefixes,
                )
                if policy == "direct" and not own_bot and historical_text is None:
                    continue
                if self._management_text(historical_text) or not old.content:
                    continue
                historical_scope = Scope(
                    scope.guild_id,
                    scope.channel_id,
                    old.author.id,
                    scope.public_at_capture,
                )
                direct_token = CURRENT_DIRECT_TRIGGER.set(own_bot or historical_text is not None)
                try:
                    self.recent.add(
                        historical_scope,
                        old.id,
                        old.author.display_name,
                        old.content,
                        role="assistant" if own_bot else "user",
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
        visuals = (
            await collect_visual_inputs(message, limits=self.vision_limits)
            if text is not None else []
        )
        target_token = TARGET_CONTEXT.set(tuple(sampled))
        visual_token = CURRENT_VISUAL_INPUTS.set(tuple(visuals))
        direct_token = CURRENT_DIRECT_TRIGGER.set(text is not None)

        # The legacy base client treats an empty normalized text as a ping-only call. Preserve
        # its trigger syntax while giving image-only calls a useful user prompt.
        original_content = None
        if text is not None and not text and visuals:
            original_content = message.content
            message.content = (message.content + " 이 이미지나 스티커를 봐줘.").strip()
        try:
            return await super().on_message(message)
        finally:
            if original_content is not None:
                message.content = original_content
            CURRENT_DIRECT_TRIGGER.reset(direct_token)
            CURRENT_VISUAL_INPUTS.reset(visual_token)
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

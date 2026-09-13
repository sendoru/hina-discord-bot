import logging

from .bot import HinaClient as BaseHinaClient
from .chat_llm import LLM
from .config import Settings
from .routing import trigger_text
from .slash_commands import install_slash_commands
from .target_context import TARGET_CONTEXT, collect
from .target_recent import TargetAwareRecentMessages

log = logging.getLogger("hina")


class HinaClient(BaseHinaClient):
    """Production Discord client wired to chat web search and slash-only controls."""

    def __init__(self, settings: Settings, *, store=None, llm=None):
        if llm is None:
            llm = LLM(settings)
        super().__init__(settings, store=store, llm=llm)
        self.recent = TargetAwareRecentMessages(budget=settings.channel_context_chars)
        install_slash_commands(self)

    @staticmethod
    def _management_text(text):
        return False

    async def command(self, message, scope, text):
        return None

    async def on_message(self, message):
        if self.user is None:
            return await super().on_message(message)
        text = trigger_text(
            message,
            self.user.id,
            self.settings.dm_always_reply,
            self.settings.call_prefixes,
        )
        sampled = await collect(message, self.user.id, text) if text is not None else []
        token = TARGET_CONTEXT.set(tuple(sampled))
        try:
            return await super().on_message(message)
        finally:
            TARGET_CONTEXT.reset(token)


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

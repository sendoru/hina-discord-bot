import logging

from hina_bot.ai.vision import CURRENT_VISUAL_INPUTS

from .bot import HinaClient as BaseHinaClient
from .chat_llm import LLM
from .config import Settings
from .routing import trigger_text
from .slash_commands import install_slash_commands
from .target_context import TARGET_CONTEXT, collect
from .target_recent import TargetAwareRecentMessages
from .vision import VisionLimits, collect_visual_inputs

log = logging.getLogger("hina")


class HinaClient(BaseHinaClient):
    """Production Discord client wired to current context, web search, and vision."""

    def __init__(self, settings: Settings, *, store=None, llm=None):
        if llm is None:
            llm = LLM(settings)
        super().__init__(settings, store=store, llm=llm)
        self.recent = TargetAwareRecentMessages(budget=settings.channel_context_chars)
        self.vision_limits = VisionLimits.from_settings(settings)
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
        visuals = (
            await collect_visual_inputs(message, limits=self.vision_limits)
            if text is not None else []
        )
        target_token = TARGET_CONTEXT.set(tuple(sampled))
        visual_token = CURRENT_VISUAL_INPUTS.set(tuple(visuals))

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

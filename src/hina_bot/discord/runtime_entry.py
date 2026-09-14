import logging

from hina_bot.ai.runtime_llm import LLM
from hina_bot.core.config import Settings
from hina_bot.core.runtime_config import RuntimeSettings
from hina_bot.core.store import Store

from . import web_bot
from .config_commands import ConfigCommands


def main():
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
    logging.getLogger("hina").setLevel(logging.INFO)
    try:
        base_settings = Settings.load()
    except ValueError as exc:
        raise SystemExit(str(exc)) from None

    store = Store(base_settings.db_path, base_settings.history_turns)
    settings = RuntimeSettings(base_settings, store)
    llm = LLM(settings)
    bot = web_bot.HinaClient(settings, store=store, llm=llm)
    bot.tree.add_command(ConfigCommands(bot))
    bot.run(settings.discord_token, log_handler=None)

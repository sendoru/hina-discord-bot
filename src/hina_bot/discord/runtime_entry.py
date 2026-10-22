import asyncio
import logging

from hina_bot.ai.providers import create_provider_client
from hina_bot.ai.runtime_llm import LLM
from hina_bot.core.admin_db import AdminDatabase
from hina_bot.core.config import Settings
from hina_bot.core.runtime_config import RuntimeSettings
from hina_bot.core.store import Store

from . import web_bot
from .shutdown import run_client


def main():
    logging.basicConfig(level=logging.WARNING, format='%(levelname)s %(name)s: %(message)s')
    logging.getLogger('hina').setLevel(logging.INFO)
    try:
        base = Settings.load()
    except ValueError as exc:
        raise SystemExit(str(exc)) from None
    store = Store.open_existing(base.db_path, base.history_turns)
    admin = None
    llm = None
    bot = None
    provider = None
    try:
        settings = RuntimeSettings(base, store)
        admin = AdminDatabase.open_existing(base.db_path)
        provider = create_provider_client(settings, settings.provider)
        llm = LLM(settings, client=provider, admin_db=admin)
        bot = web_bot.HinaClient(settings, store=store, llm=llm)
        settings.capture_startup_snapshot()
        run_client(bot, settings.discord_token)
    finally:
        # bot.run normally closes these. Initialization errors occur before run.
        try:
            if bot is not None and not bot.is_closed():
                asyncio.run(bot.close())
            elif bot is None and llm is not None:
                asyncio.run(llm.close())
            elif llm is None and provider is not None:
                asyncio.run(provider.close())
        finally:
            if admin is not None:
                admin.close()
            store.close()

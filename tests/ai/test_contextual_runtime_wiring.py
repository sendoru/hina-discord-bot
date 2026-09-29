import inspect

import hina_bot.discord.bot as base_bot_module
import hina_bot.discord.web_bot as web_bot_module
from hina_bot.ai.information_pipeline import LLM as ChatLLM
from hina_bot.ai.runtime_llm import LLM as RuntimeLLM
from hina_bot.discord.bot import HinaClient as BaseHinaClient
from hina_bot.discord.runtime_entry import LLM as EntryLLM
from hina_bot.discord.web_bot import HinaClient as ProductionHinaClient


def test_runtime_llm_owns_followup_routing_without_wrapper_class():
    assert issubclass(RuntimeLLM, ChatLLM)
    assert EntryLLM is RuntimeLLM
    assert RuntimeLLM.answer is not ChatLLM.answer


def test_discord_clients_require_composed_llm():
    assert (
        inspect.signature(BaseHinaClient.__init__).parameters["llm"].default
        is inspect.Parameter.empty
    )
    assert (
        inspect.signature(ProductionHinaClient.__init__).parameters["llm"].default
        is inspect.Parameter.empty
    )


def test_runtime_entry_is_the_only_discord_module_entrypoint():
    assert "main" not in base_bot_module.__dict__
    assert "main" not in web_bot_module.__dict__

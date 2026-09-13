"""Hina Discord bot package."""

import sys
from importlib import abc, import_module, util

# Preserve the existing public LLM behavior after splitting implementation modules
# into feature-oriented subpackages.
from .ai import llm as _llm
from .ai.chat_llm import LLM as _ChatLLM

_llm.LLM = _ChatLLM

# Temporary compatibility for the repository's existing flat import paths. Runtime
# entry points use the new package paths; these aliases keep scripts/tests and local
# integrations working while callers migrate naturally.
_LEGACY_MODULES = {
    "admin_db": "hina_bot.core.admin_db",
    "admin_list": "hina_bot.core.admin_list",
    "config": "hina_bot.core.config",
    "emojis": "hina_bot.core.emojis",
    "instructions": "hina_bot.core.instructions",
    "knowledge_ingest": "hina_bot.core.knowledge_ingest",
    "lore": "hina_bot.core.lore",
    "recent": "hina_bot.core.recent",
    "routing": "hina_bot.core.routing",
    "runtime_knowledge": "hina_bot.core.runtime_knowledge",
    "runtime_migration": "hina_bot.core.runtime_migration",
    "store": "hina_bot.core.store",
    "chat_llm": "hina_bot.ai.chat_llm",
    "chat_llm_v2": "hina_bot.ai.chat_llm_v2",
    "llm": "hina_bot.ai.llm",
    "rp_output_policy": "hina_bot.ai.rp_output_policy",
    "runtime_llm": "hina_bot.ai.runtime_llm",
    "usage": "hina_bot.ai.usage",
    "web_search_runtime": "hina_bot.ai.web_search_runtime",
    "web_search_text": "hina_bot.ai.web_search_text",
    "bot": "hina_bot.discord.bot",
    "chatlog_commands": "hina_bot.discord.chatlog_commands",
    "emoji_commands": "hina_bot.discord.emoji_commands",
    "instruction_commands": "hina_bot.discord.instruction_commands",
    "knowledge_commands": "hina_bot.discord.knowledge_commands",
    "memory_commands": "hina_bot.discord.memory_commands",
    "output_safety": "hina_bot.discord.output_safety",
    "runtime_entry": "hina_bot.discord.runtime_entry",
    "slash_commands": "hina_bot.discord.slash_commands",
    "web_bot": "hina_bot.discord.web_bot",
    "eval_runner": "hina_bot.tooling.eval_runner",
    "lore_cli": "hina_bot.tooling.lore_cli",
    "lore_pipeline": "hina_bot.tooling.lore_pipeline",
    "lore_web": "hina_bot.tooling.lore_web",
}


class _LegacyAliasLoader(abc.Loader):
    def __init__(self, target: str):
        self.target = target

    def create_module(self, spec):
        return import_module(self.target)

    def exec_module(self, module):
        return None


class _LegacyAliasFinder(abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        prefix = __name__ + "."
        if not fullname.startswith(prefix):
            return None
        target_name = _LEGACY_MODULES.get(fullname[len(prefix):])
        if target_name is None:
            return None
        return util.spec_from_loader(fullname, _LegacyAliasLoader(target_name))


if not any(isinstance(finder, _LegacyAliasFinder) for finder in sys.meta_path):
    sys.meta_path.insert(0, _LegacyAliasFinder())
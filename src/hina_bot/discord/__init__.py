"""Discord transport, slash commands, and runtime entry points."""

import sys
from importlib import import_module

_CORE_ALIASES = (
    "admin_db",
    "admin_list",
    "config",
    "emojis",
    "instructions",
    "knowledge_ingest",
    "lore",
    "recent",
    "routing",
    "runtime_knowledge",
    "store",
)
_AI_ALIASES = (
    "chat_llm",
    "chat_llm_v2",
    "freshness",
    "llm",
    "rp_output_policy",
    "runtime_context",
    "runtime_llm",
    "usage",
    "web_search_runtime",
    "web_search_text",
)

for _name in _CORE_ALIASES:
    _module = import_module(f"hina_bot.core.{_name}")
    sys.modules[f"{__name__}.{_name}"] = _module
    globals()[_name] = _module
for _name in _AI_ALIASES:
    _module = import_module(f"hina_bot.ai.{_name}")
    sys.modules[f"{__name__}.{_name}"] = _module
    globals()[_name] = _module

del _name, _module

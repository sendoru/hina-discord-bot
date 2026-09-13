"""Compatibility surface for the current chat LLM implementation.

The implementation lives in chat_llm_v2; keep this module so existing imports such as
``hina_bot.chat_llm`` continue to use the same runtime behavior.
"""

from .chat_llm_v2 import LLM

__all__ = ["LLM"]

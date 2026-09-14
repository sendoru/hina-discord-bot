"""Production runtime combining contextual follow-up routing with runtime policies."""

from .contextual_chat_llm import LLM as ContextualChatLLM
from .runtime_llm import LLM as RuntimeLLM


class LLM(RuntimeLLM, ContextualChatLLM):
    """Compose runtime vision/memory behavior with contextual chat routing."""


__all__ = ["LLM"]

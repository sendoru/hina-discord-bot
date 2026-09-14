"""Stable name for the layer that assembles the final model request."""

from .chat_llm_v2 import LLM as RequestAssembler

LLM = RequestAssembler

__all__ = ["LLM", "RequestAssembler"]

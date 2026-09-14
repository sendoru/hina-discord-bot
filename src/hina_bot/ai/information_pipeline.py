"""Stable name for information classification, lore retrieval, and web routing."""

from .chat_llm import LLM as InformationPipeline

LLM = InformationPipeline

__all__ = ["InformationPipeline", "LLM"]

"""Compatibility import for the information-routing pipeline.

New code should import :mod:`hina_bot.ai.information_pipeline` directly.
"""

from .information_pipeline import LLM, InformationPipeline

__all__ = ["LLM", "InformationPipeline"]

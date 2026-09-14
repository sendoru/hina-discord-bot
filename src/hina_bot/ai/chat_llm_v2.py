"""Compatibility import for the former v2 chat layer.

Production behavior now lives in :mod:`hina_bot.ai.information_pipeline`; request construction
lives in :mod:`hina_bot.ai.request_assembly`.
"""

from .information_pipeline import InformationPipeline
from .request_assembly import RequestAssembler

LLM = InformationPipeline

__all__ = ["LLM", "RequestAssembler"]

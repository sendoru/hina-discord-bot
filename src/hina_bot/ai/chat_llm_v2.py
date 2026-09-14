"""Compatibility import for the request-assembly layer.

New code should import :mod:`hina_bot.ai.request_assembly` directly.
"""

from .request_assembly import LLM, RequestAssembler

__all__ = ["LLM", "RequestAssembler"]

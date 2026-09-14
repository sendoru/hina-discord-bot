"""Production chat layer that routes elliptical follow-ups using nearby context."""

from contextvars import ContextVar

from .chat_llm import LLM as BaseLLM
from .contextual_routing import build_query, find_anchor, is_followup

_VISIBLE_CONTENT = ContextVar("hina_visible_followup_content", default=None)


class _ResponsesProxy:
    def __init__(self, responses):
        self._responses = responses

    async def create(self, **kwargs):
        visible = _VISIBLE_CONTENT.get()
        items = kwargs.get("input")
        if visible is not None and isinstance(items, list) and items:
            tail = items[-1]
            if isinstance(tail, dict) and tail.get("role") == "user":
                rewritten = list(items)
                rewritten_tail = dict(tail)
                rewritten_tail["content"] = visible
                rewritten[-1] = rewritten_tail
                kwargs = dict(kwargs)
                kwargs["input"] = rewritten
        return await self._responses.create(**kwargs)


class _ClientProxy:
    def __init__(self, client):
        self._client = client
        self.responses = _ResponsesProxy(client.responses)

    def __getattr__(self, name):
        return getattr(self._client, name)


class LLM(BaseLLM):
    """Use a routing-only expanded query while preserving the user's exact visible turn."""

    def __init__(self, settings, client=None):
        super().__init__(settings, client=client)
        if not isinstance(self.client, _ClientProxy):
            self.client = _ClientProxy(self.client)

    async def answer(
        self,
        store,
        scope,
        name: str,
        content: str,
        public_context: list | None = None,
        channel_context: list | None = None,
        emoji_catalog: list | None = None,
        use_memory: bool = True,
    ) -> str:
        rows = channel_context or []
        anchor = (
            find_anchor(store, scope, rows, use_memory=use_memory)
            if is_followup(content)
            else ""
        )
        routing_query = build_query(content, anchor)
        if routing_query == content:
            return await super().answer(
                store,
                scope,
                name,
                content,
                public_context=public_context,
                channel_context=channel_context,
                emoji_catalog=emoji_catalog,
                use_memory=use_memory,
            )

        token = _VISIBLE_CONTENT.set(content)
        try:
            return await super().answer(
                store,
                scope,
                name,
                routing_query,
                public_context=public_context,
                channel_context=channel_context,
                emoji_catalog=emoji_catalog,
                use_memory=use_memory,
            )
        finally:
            _VISIBLE_CONTENT.reset(token)


__all__ = ["LLM"]

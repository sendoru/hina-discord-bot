"""Request-scoped lookup of authorized P4 server memories."""

import time

from hina_bot.ai.local_tools import LocalToolError, LocalToolSpec
from hina_bot.ai.prompts import load_prompt
from hina_bot.core.memory_context import CURRENT_CONTEXT_PROVENANCE
from hina_bot.core.server_memory_selection import CURRENT_SERVER_READER

from .turn_provenance import CURRENT_TURN_PROVENANCE

RECALL_CALLS_PER_ANSWER = 2

MEMORY_RECALL_TOOL = LocalToolSpec(
    name='recall_memory',
    description=load_prompt("server_recall_tool.md", section="memory").strip(),
    parameters={
        'type': 'object',
        'properties': {'query': {'type': 'string', 'minLength': 1, 'maxLength': 100},
                       'scope': {'type': 'string', 'enum': ['server']}},
        'required': ['query'],
        'additionalProperties': False,
    },
)


class MemoryRecall:
    """A handler closure with a per-answer call budget for one request."""

    def __init__(self, *, events=None):
        self.events = events
        self.calls = 0

    async def __call__(self, arguments):
        started = time.perf_counter()
        self.calls += 1
        try:
            if self.calls > RECALL_CALLS_PER_ANSWER:
                raise LocalToolError('budget_exhausted')
            if set(arguments) - {'query', 'scope'}:
                raise LocalToolError('invalid_arguments')
            query = str(arguments.get('query') or '').strip()[:100]
            if not query:
                raise LocalToolError('invalid_arguments')
            if arguments.get('scope', 'server') != 'server':
                raise LocalToolError('invalid_arguments')
            reader = CURRENT_SERVER_READER.get()
            if reader is None:
                raise LocalToolError('memory_disabled')
            rows = await reader(query, lookup=True)
            self._record(rows)
            status = 'ok' if rows else 'empty'
            self._emit(status, len(rows), started, target='server')
            return {'status': status, 'memories': rows}
        except LocalToolError as exc:
            self._emit('error', 0, started, error_code=exc.code)
            raise

    def _record(self, rows) -> None:
        provenance = dict(CURRENT_CONTEXT_PROVENANCE.get() or {})
        provenance["server_memory"] = [
            *provenance.get("server_memory", []),
            *({"id": row["id"], "store": row["store"], "tier": "S3"} for row in rows),
        ]
        lookup = dict(provenance.get('memory_lookup') or {})
        lookup['calls'] = int(lookup.get('calls', 0)) + 1
        lookup.setdefault('items', [])
        provenance['memory_lookup'] = lookup
        CURRENT_CONTEXT_PROVENANCE.set(provenance)
        turn = CURRENT_TURN_PROVENANCE.get()
        if turn is not None:
            turn['memory_lookup'] = lookup

    def _emit(self, status: str, returned: int, started: float, **fields) -> None:
        if self.events is not None:
            self.events.emit(
                'memory.recall', status=status, returned_count=returned,
                elapsed_ms=round((time.perf_counter() - started) * 1000), **fields,
            )


def register_memory_recall_tool(registry, *, events=None) -> None:
    if CURRENT_SERVER_READER.get() is not None:
        registry.register(MEMORY_RECALL_TOOL, MemoryRecall(events=events))


__all__ = ['MEMORY_RECALL_TOOL', 'MemoryRecall', 'register_memory_recall_tool']

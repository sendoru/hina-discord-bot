"""Provider-neutral local function tool definitions and bounded execution."""

from __future__ import annotations

import inspect
import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

JsonObject = dict[str, Any]
ToolHandler = Callable[[JsonObject], Any | Awaitable[Any]]


class LocalToolError(RuntimeError):
    """Expected local-tool failure with a model-safe error code."""

    def __init__(self, code: str):
        self.code = str(code or "tool_error")[:80]
        super().__init__(self.code)


class ToolRoundLimitError(RuntimeError):
    """The model kept requesting local tools beyond the configured bound."""


@dataclass(frozen=True)
class LocalToolSpec:
    name: str
    description: str
    parameters: JsonObject

    def __post_init__(self) -> None:
        name = self.name.strip()
        if not name or len(name) > 64:
            raise ValueError("Local tool name must contain 1..64 characters")
        if not isinstance(self.parameters, dict) or self.parameters.get("type") != "object":
            raise ValueError("Local tool parameters must be an object JSON schema")

    def provider_schema(self) -> dict:
        return {
            "type": "function",
            "name": self.name,
            "description": self.description,
            "parameters": self.parameters,
        }


@dataclass(frozen=True)
class LocalToolCall:
    call_id: str
    name: str
    arguments: JsonObject | None
    error_code: str = ""


@dataclass(frozen=True)
class LocalToolResult:
    call_id: str
    name: str
    output: Any
    is_error: bool = False

    def provider_input(self) -> dict:
        payload = {"ok": not self.is_error, "result": self.output}
        return {
            "type": "function_call_output",
            "call_id": self.call_id,
            "name": self.name,
            "output": json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
        }


@dataclass(frozen=True)
class ToolLoopResult:
    response: Any
    responses: tuple[Any, ...]
    local_calls: int


@dataclass(frozen=True)
class _RegisteredTool:
    spec: LocalToolSpec
    handler: ToolHandler


class LocalToolRegistry:
    """Explicit allowlist of local functions that the model may request."""

    def __init__(self):
        self._tools: dict[str, _RegisteredTool] = {}

    def register(self, spec: LocalToolSpec, handler: ToolHandler) -> None:
        if spec.name in self._tools:
            raise ValueError(f"Duplicate local tool: {spec.name}")
        self._tools[spec.name] = _RegisteredTool(spec, handler)

    def schemas(self) -> list[dict]:
        return [registered.spec.provider_schema() for registered in self._tools.values()]

    def __bool__(self) -> bool:
        return bool(self._tools)

    async def execute(self, call: LocalToolCall) -> LocalToolResult:
        registered = self._tools.get(call.name)
        if registered is None:
            return LocalToolResult(call.call_id, call.name, {"error": "unknown_tool"}, True)
        if call.error_code or call.arguments is None:
            return LocalToolResult(
                call.call_id,
                call.name,
                {"error": call.error_code or "invalid_arguments"},
                True,
            )
        try:
            output = registered.handler(dict(call.arguments))
            if inspect.isawaitable(output):
                output = await output
            json.dumps(output, ensure_ascii=False)
            return LocalToolResult(call.cal_id, call.name, output)
        except LocalToolError as exc:
            return LocalToolResult(call.call_id, call.name, {"error": exc.code}, True)
        except Exception:
            return LocalToolResult(
                call.call_id,
                call.name,
                {"error": "internal_tool_error"},
                True,
            )


def _field(value, name: str, default=None):
    if isinstance(value, dict):
        return value.get(name, default)
    return getattr(value, name, default)


def extract_local_tool_calls(response) -> tuple[LocalToolCall, ...]:
    """Normalize provider function-call output items into local calls."""

    calls = []
    for item in _field(response, "output", ()) or ():
        if _field(item, "type") != "function_call":
            continue
        call_id = str(_field(item, "call_id") or _field(item, "id") or "")
        name = str(_field(item, "name") or "")
        raw_arguments = _field(item, "arguments", {})
        error_code = ""
        if not call_id or not name:
            continue
        if isinstance(raw_arguments, dict):
            arguments = dict(raw_arguments)
        elif isinstance(raw_arguments, str):
            try:
                parsed = json.loads(raw_arguments)
            except (TypeError, ValueError):
                parsed = None
            if isinstance(parsed, dict):
                arguments = parsed
            else:
                arguments = None
                error_code = "invalid_arguments"
        else:
            arguments = None
            error_code = "invalid_arguments"
        calls.append(LocalToolCall(call_id, name, arguments, error_code))
    return tuple(calls)


def _normalized_function_call(call: LocalToolCall) -> dict:
    return {
        "type": "function_call",
        "call_id": call.call_id,
        "name": call.name,
        "arguments": json.dumps(
            call.arguments or {},
            ensure_ascii=False,
            separators=(",", ":"),
        ),
    }


def continuation_request(
    request: dict,
    calls: tuple[LocalToolCall, ...],
    results: tuple[LocalToolResult, ...],
) -> dict:
    """Append one local-tool round using the provider-neutral Responses shape."""

    previous_input = request.get("input", [])
    if isinstance(previous_input, list):
        next_input = list(previous_input)
    elif isinstance(previous_input, str):
        next_input = [{"role": "user", "content": previous_input}]
    else:
        next_input = [{"role": "user", "content": str(previous_input)}]
    next_input.extend(_normalized_function_call(call) for call in calls)
    next_input.extend(result.provider_input() for result in results)

    followup = dict(request)
    followup["input"] = next_input
    followup["tool_choice"] = "auto"
    return followup


class LocalToolExecutor:
    """Run model-requested local tools with a strict round bound."""

    def __init__(self, registry: LocalToolRegistry, *, max_rounds: int = 4):
        if max_rounds < 1:
            raise ValueError("max_rounds must be at least 1")
        self.registry = registry
        self.max_rounds = int(max_rounds)

    async def run(self, send, request: dict) -> ToolLoopResult:
        """Call send(request) until the model stops requesting local functions."""

        current_request = dict(request)
        responses = []
        local_calls = 0

        for round_index in range(self.max_rounds + 1):
            response = await send(current_request)
            responses.append(response)
            calls = extract_local_tool_calls(response)
            if not calls:
                return ToolLoopResult(response, tuple(responses), local_calls)
            if round_index >= self.max_rounds:
                raise ToolRoundLimitError("local tool round limit exceeded")

            results = tuple([await self.registry.execute(call) for call in calls])
            local_calls += len(calls)
            current_request = continuation_request(current_request, calls, results)

        raise ToolRoundLimitError("local tool round limit exceeded")


__all__ = [
    "LocalToolCall",
    "LocalToolError",
    "LocalToolExecutor",
    "LocalToolRegistry",
    "LocalToolResult",
    "LocalToolSpec",
    "ToolLoopResult",
    "ToolRoundLimitError",
    "continuation_request",
    "extract_local_tool_calls",
]

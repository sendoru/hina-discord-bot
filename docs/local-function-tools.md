# Local function tools

The runtime separates two kinds of model tools:

- Provider-managed tools, such as web search. The provider executes these.
- Local function tools. The model chooses a function, but the bot process executes an explicitly
  registered handler and sends a structured result back to the model.

## Provider-neutral shape

Local tools use a common function schema:

```json
{
  "type": "function",
  "name": "example",
  "description": "What the function does.",
  "parameters": {
    "type": "object",
    "properties": {}
  }
}
```

OpenAI-compatible providers receive this shape directly. Gemini's adapter translates it to
Interactions API function declarations and normalizes returned function-call steps back into the
same response shape.

## Registry and execution boundary

`LocalToolRegistry` is an explicit allowlist. A function cannot execute merely because the model
invented its name.

Handlers receive a JSON object and must return JSON-serializable data. They may be synchronous or
asynchronous. Expected failures may raise `LocalToolError` with a safe error code. Unexpected
exceptions are converted to `internal_tool_error`; exception messages are never sent to the model.

The executor:

1. sends the model request,
2. extracts function calls,
3. executes registered handlers,
4. appends function-call outputs,
5. lets the model continue with `tool_choice=auto`.

The loop is bounded to four local-tool rounds by default. Exceeding the limit raises
`ToolRoundLimitError`.

## Provider adapters

- OpenAI: standard Responses function tool shape passes through natively.
- Gemini: function declarations, calls, and results are translated to/from Interactions API steps.
- OpenRouter: web search remains a provider plugin while local function declarations remain model
  tools, so both may coexist in an auto-selection request.

## Current rollout

This abstraction intentionally registers no production local tools yet. Therefore merging this
change alone does not expose any new capability to chat requests.

The next planned step is to register a deterministic calculator and include its schema in normal
chat requests with auto tool selection.

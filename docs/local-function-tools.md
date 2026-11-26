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
5. lets the model continue with `tool_choice=auto` while local-tool rounds remain,
6. after the last permitted local-tool round, makes one **tool-free final answer request**
   with the collected tool results and original conversation state.

The loop is bounded to four local-tool execution rounds by default. On the final
continuation, all tool schemas and tool-choice options are omitted, including managed
tools (such as web search). This works for adapters that do not forward `tool_choice`
to the provider and avoids another `requires_action` loop. The finalization policy
is maintained in `src/hina_bot/prompts/integration/local_tool_finalization.md` so the
model can answer from available evidence without inventing missing history.
If the provider still emits a function call without being offered a tool, the
existing `ToolRoundLimitError` safeguard remains in effect. Provider failures,
timeouts, and invalid/empty final responses still follow existing error handling.

## Provider adapters

- OpenAI: standard Responses function tool shape passes through natively.
- Gemini: function declarations, calls, and results are translated to/from Interactions API steps.
- OpenRouter: web search uses the OpenRouter server tool while local function declarations remain model
  tools, so both may coexist in an auto-selection request.

## Current rollout

모든 Discord 답변 요청에 `get_current_channel_members` 로컬 function tool 스키마를
등록합니다. 실제 호출은 모델이 판단하며, 현재 guild 텍스트 채널·공개/비공개 스레드에서만
멤버를 조회합니다. 다른 공간에서는 안전한 오류를 반환합니다. 모델이 현재 채널의
사람을 이름·별명으로 식별하거나 정확한 mention ID가 필요하다고 판단한 경우에만
이 도구를 호출합니다. 멤버 명단은 초기 answer request에 포함되지 않으며, 실제 tool call 이후의
function output으로만 provider에 전달됩니다.

도구 자체의 Discord 접근·privacy 범위는 [채널 멤버 도구](channel-member-tool.md)를 참고하세요.

모든 Discord 답변 요청의 registry에는 `get_chat_history`를 사전 내용/채널 유형 판정 없이
등록합니다. 모델이 실제로 호출했을 때 현재 채널·공개/비공개 스레드 또는 1:1 DM의 접근 가능 범위를
검증하며, 지원하지 않는 채널에서는 안전한 도구 오류를 반환합니다. 자동 문맥과 reset 이전 명시 조회의 구분, 조회 예산·권한·출처 처리는
[과거 대화 조회 도구](chat-history-tool.md)를 참고하세요.

서버 조회가 가능한 요청에서는 `recall_memory`로 P4 서버 공유 기억을 조회합니다. 개인 기억 읽기 모드와 독립적으로 동작하며, 호출할 때 원 채널의 열람권을 확인합니다. 개인 기억은 답변을 만들기 전에 선택합니다. [구조화 기억](structured-memory.md#personal-memory-read-modes)

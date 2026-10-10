"""Model-provider adapters for the bot runtime."""

import json
import logging
import mimetypes
from types import SimpleNamespace as NS
from urllib.parse import urlsplit

import httpx
from openai import AsyncOpenAI

GEMINI_INTERACTIONS_URL = "https://generativelanguage.googleapis.com/v1beta/interactions"
GEMINI_GENERATE_CONTENT_URL = (
    "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
)
OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
SUPPORTED_PROVIDERS = frozenset({"openai", "gemini", "openrouter"})
log = logging.getLogger("hina")


def normalize_provider(value: str) -> str:
    provider = value.strip().lower()
    if provider not in SUPPORTED_PROVIDERS:
        allowed = ", ".join(sorted(SUPPORTED_PROVIDERS))
        raise ValueError(f"지원하지 않는 LLM provider입니다: {value!r} (지원: {allowed})")
    return provider


def _safe_error_text(value, limit: int = 300) -> str:
    if not isinstance(value, str):
        return ""
    return " ".join(value.split())[:limit]


class ProviderAPIError(RuntimeError):
    """Provider error with diagnostics that are safe to write to content-free logs."""

    def __init__(self, provider: str, status_code: int, *, code: str = "", message: str = ""):
        self.provider = provider
        self.status_code = status_code
        self.error_code = _safe_error_text(code, 100)
        self.error_message = _safe_error_text(message)
        diagnostic = f"{provider} HTTP {status_code}"
        if self.error_code:
            diagnostic += f" {self.error_code}"
        if self.error_message:
            diagnostic += f": {self.error_message}"
        self.safe_diagnostic = diagnostic
        super().__init__(diagnostic)


def _gemini_http_error(response: httpx.Response) -> ProviderAPIError:
    code = ""
    message = ""
    try:
        data = response.json()
    except ValueError:
        data = None
    if isinstance(data, dict):
        error = data.get("error")
        if isinstance(error, dict):
            raw_code = error.get("status") or error.get("code")
            if raw_code is not None:
                code = str(raw_code)
            message = error.get("message") if isinstance(error.get("message"), str) else ""
    return ProviderAPIError("gemini", response.status_code, code=code, message=message)


def _gemini_image_block(image_url: str, resolution: str = ""):
    if image_url.startswith("data:") and ";base64," in image_url:
        header, data = image_url.split(",", 1)
        mime_type = header[5:].split(";", 1)[0]
        if not mime_type.startswith("image/") or not data:
            return None
        image = {"type": "image", "data": data, "mime_type": mime_type}
    else:
        mime_type = mimetypes.guess_type(urlsplit(image_url).path)[0] or "image/png"
        if not mime_type.startswith("image/"):
            mime_type = "image/png"
        image = {"type": "image", "uri": image_url, "mime_type": mime_type}

    if resolution in {"low", "medium", "high", "ultra_high"}:
        image["resolution"] = resolution
    return image


def _gemini_content(value):
    if isinstance(value, str):
        return [{"type": "text", "text": value}] if value else []
    if not isinstance(value, list):
        text = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
        return [{"type": "text", "text": text}]

    blocks = []
    for item in value:
        if isinstance(item, str):
            if item:
                blocks.append({"type": "text", "text": item})
            continue
        if not isinstance(item, dict):
            continue
        block_type = item.get("type")
        if block_type in {"input_text", "text"}:
            text = item.get("text")
            if isinstance(text, str) and text:
                blocks.append({"type": "text", "text": text})
            continue
        if block_type == "input_image":
            image_url = item.get("image_url")
            if isinstance(image_url, str) and image_url:
                image = _gemini_image_block(
                    image_url,
                    str(item.get("resolution") or ""),
                )
                if image is not None:
                    blocks.append(image)
            continue
        text = item.get("text")
        if isinstance(text, str) and text:
            blocks.append({"type": "text", "text": text})
    return blocks


def _gemini_input(value):
    if isinstance(value, str):
        return value
    if not isinstance(value, list):
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))

    steps = []
    function_names = {}
    for item in value:
        if not isinstance(item, dict):
            continue
        item_type = item.get("type")
        if item_type in {
            "thought",
            "model_output",
            "code_execution_call",
            "code_execution_result",
            "google_search_call",
            "google_search_result",
            "file_search_call",
            "file_search_result",
        }:
            steps.append(dict(item))
            continue
        if (
            item_type == "function_call"
            and item.get("id")
            and not item.get("call_id")
            and isinstance(item.get("arguments"), dict)
        ):
            call_id = str(item.get("id") or "")
            name = str(item.get("name") or "")
            if call_id and name:
                function_names[call_id] = name
                steps.append(dict(item))
            continue
        if item_type == "function_call":
            raw_arguments = item.get("arguments", {})
            if isinstance(raw_arguments, str):
                try:
                    raw_arguments = json.loads(raw_arguments)
                except ValueError:
                    raw_arguments = {}
            if not isinstance(raw_arguments, dict):
                raw_arguments = {}
            call_id = str(item.get("call_id") or item.get("id") or "")
            name = str(item.get("name") or "")
            if call_id and name:
                function_names[call_id] = name
                steps.append({
                    "type": "function_call",
                    "id": call_id,
                    "name": name,
                    "arguments": raw_arguments,
                })
            continue
        if item_type == "function_call_output":
            call_id = str(item.get("call_id") or "")
            if not call_id:
                continue
            output = item.get("output", "")
            if not isinstance(output, str):
                output = json.dumps(output, ensure_ascii=False, separators=(",", ":"))
            result = {
                "type": "function_result",
                "call_id": call_id,
                "result": [{"type": "text", "text": output}],
            }
            name = item.get("name")
            if not isinstance(name, str) or not name:
                name = function_names.get(call_id, "")
            if name:
                result["name"] = name
            steps.append(result)
            continue

        role = item.get("role", "user")
        content = _gemini_content(item.get("content", ""))
        if not content:
            continue
        step_type = "model_output" if role == "assistant" else "user_input"
        steps.append({"type": step_type, "content": content})
    return steps



def _gemini_generate_content_parts(value):
    parts = []
    for block in _gemini_content(value):
        block_type = block.get("type")
        if block_type == "text":
            parts.append({"text": block["text"]})
            continue
        if block_type != "image":
            continue
        mime_type = block.get("mime_type") or "image/png"
        if block.get("data"):
            parts.append({
                "inline_data": {
                    "mime_type": mime_type,
                    "data": block["data"],
                }
            })
        elif block.get("uri"):
            parts.append({
                "file_data": {
                    "mime_type": mime_type,
                    "file_uri": block["uri"],
                }
            })
    return parts


def _gemini_generate_content_contents(value):
    if isinstance(value, str):
        return [{"role": "user", "parts": [{"text": value}]}]
    if not isinstance(value, list):
        text = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
        return [{"role": "user", "parts": [{"text": text}]}]

    contents = []
    function_names = {}

    def append(role: str, parts: list[dict]):
        if not parts:
            return
        if contents and contents[-1]["role"] == role:
            contents[-1]["parts"].extend(parts)
        else:
            contents.append({"role": role, "parts": parts})

    for item in value:
        if not isinstance(item, dict):
            continue
        item_type = item.get("type")
        if item_type == "function_call":
            raw_arguments = item.get("arguments", {})
            if isinstance(raw_arguments, str):
                try:
                    raw_arguments = json.loads(raw_arguments)
                except ValueError:
                    raw_arguments = {}
            if not isinstance(raw_arguments, dict):
                raw_arguments = {}
            call_id = str(item.get("call_id") or item.get("id") or "")
            name = str(item.get("name") or "")
            if call_id and name:
                function_names[call_id] = name
                append("model", [{
                    "functionCall": {
                        "id": call_id,
                        "name": name,
                        "args": raw_arguments,
                    }
                }])
            continue
        if item_type == "function_call_output":
            call_id = str(item.get("call_id") or "")
            name = str(item.get("name") or function_names.get(call_id) or "")
            if not call_id or not name:
                continue
            output = item.get("output", "")
            if isinstance(output, str):
                try:
                    response = json.loads(output)
                except ValueError:
                    response = {"output": output}
            elif isinstance(output, dict):
                response = output
            else:
                response = {"output": output}
            if not isinstance(response, dict):
                response = {"output": response}
            append("user", [{
                "functionResponse": {
                    "id": call_id,
                    "name": name,
                    "response": response,
                }
            }])
            continue

        role = "model" if item.get("role") == "assistant" else "user"
        append(role, _gemini_generate_content_parts(item.get("content", "")))

    return contents


def _gemini_generate_content_schema(value):
    """Translate JSON Schema to the subset accepted by generateContent tools."""
    if isinstance(value, dict):
        return {
            key: _gemini_generate_content_schema(item)
            for key, item in value.items()
            if key != "additionalProperties"
        }
    if isinstance(value, list):
        return [_gemini_generate_content_schema(item) for item in value]
    return value


def _gemini_dict_field(value: dict, *names: str, default=None):
    for name in names:
        if name in value:
            return value[name]
    return default


def _gemini_grounding_citations(metadata: dict) -> list[tuple[str, str]]:
    citations = []
    seen_urls = set()
    chunks = _gemini_dict_field(
        metadata,
        "groundingChunks",
        "grounding_chunks",
        default=[],
    ) or []
    for chunk in chunks:
        if not isinstance(chunk, dict):
            continue
        web = chunk.get("web")
        if not isinstance(web, dict):
            continue
        url = web.get("uri") or web.get("url")
        if not isinstance(url, str) or not url or url in seen_urls:
            continue
        seen_urls.add(url)
        title = web.get("title") if isinstance(web.get("title"), str) else ""
        citations.append((url, title))
    return citations


def _gemini_generate_content_output(data: dict):
    candidates = data.get("candidates") or []
    candidate = candidates[0] if candidates and isinstance(candidates[0], dict) else {}
    content = candidate.get("content") if isinstance(candidate.get("content"), dict) else {}
    parts = content.get("parts") or []
    metadata = _gemini_dict_field(
        candidate,
        "groundingMetadata",
        "grounding_metadata",
        default={},
    )
    if not isinstance(metadata, dict):
        metadata = {}

    output = []
    final_text_pieces = []
    last_text_part = None
    web_search_calls = 0
    last_code_id = ""

    for index, part in enumerate(parts):
        if not isinstance(part, dict) or part.get("thought") is True:
            continue

        tool_call = _gemini_dict_field(part, "toolCall", "tool_call")
        if isinstance(tool_call, dict):
            tool_type = str(
                _gemini_dict_field(tool_call, "toolType", "tool_type", default="")
            )
            if tool_type.startswith("GOOGLE_SEARCH"):
                output.append(NS(type="web_search_call"))
                web_search_calls += 1
            final_text_pieces = []
            last_text_part = None
            continue

        tool_response = _gemini_dict_field(part, "toolResponse", "tool_response")
        if isinstance(tool_response, dict):
            final_text_pieces = []
            last_text_part = None
            continue

        executable = _gemini_dict_field(part, "executableCode", "executable_code")
        if isinstance(executable, dict):
            code_id = str(executable.get("id") or f"code_{index}")
            last_code_id = code_id
            output.append(NS(
                type="code_execution_call",
                id=code_id,
                arguments={
                    "code": executable.get("code") or "",
                    "language": executable.get("language") or "",
                },
            ))
            final_text_pieces = []
            last_text_part = None
            continue

        code_result = _gemini_dict_field(
            part,
            "codeExecutionResult",
            "code_execution_result",
        )
        if isinstance(code_result, dict):
            outcome = str(code_result.get("outcome") or "")
            call_id = str(code_result.get("id") or last_code_id)
            output.append(NS(
                type="code_execution_result",
                call_id=call_id,
                result=code_result.get("output") or "",
                is_error=bool(outcome and outcome != "OUTCOME_OK"),
            ))
            final_text_pieces = []
            last_text_part = None
            continue

        function_call = _gemini_dict_field(part, "functionCall", "function_call")
        if isinstance(function_call, dict):
            call_id = str(function_call.get("id") or "")
            name = str(function_call.get("name") or "")
            if call_id and name:
                output.append(NS(
                    type="function_call",
                    id=call_id,
                    call_id=call_id,
                    name=name,
                    arguments=json.dumps(
                        function_call.get("args") or {},
                        ensure_ascii=False,
                        separators=(",", ":"),
                    ),
                ))
            final_text_pieces = []
            last_text_part = None
            continue

        text = part.get("text")
        if not isinstance(text, str) or not text:
            continue
        normalized_part = NS(type="output_text", text=text, annotations=[])
        output.append(NS(type="message", content=[normalized_part]))
        final_text_pieces.append(text)
        last_text_part = normalized_part

    queries = _gemini_dict_field(
        metadata,
        "webSearchQueries",
        "web_search_queries",
        default=[],
    ) or []
    citations = _gemini_grounding_citations(metadata)
    if web_search_calls == 0 and (queries or citations):
        output.insert(0, NS(type="web_search_call"))
        web_search_calls = 1

    if last_text_part is not None and citations:
        for url, title in citations:
            suffix = f" ({_citation_label(url)})"
            start = len(last_text_part.text)
            last_text_part.text += suffix
            last_text_part.annotations.append(NS(
                type="url_citation",
                start_index=start,
                end_index=len(last_text_part.text),
                url=url,
                title=title,
            ))
        if final_text_pieces:
            final_text_pieces[-1] = last_text_part.text

    usage_data = _gemini_dict_field(
        data,
        "usageMetadata",
        "usage_metadata",
        default={},
    )
    if not isinstance(usage_data, dict):
        usage_data = {}
    usage = NS(
        input_tokens=_gemini_dict_field(
            usage_data,
            "promptTokenCount",
            "prompt_token_count",
        ),
        output_tokens=_gemini_dict_field(
            usage_data,
            "candidatesTokenCount",
            "candidates_token_count",
        ),
        total_tokens=_gemini_dict_field(
            usage_data,
            "totalTokenCount",
            "total_token_count",
        ),
        input_tokens_details=NS(cached_tokens=_gemini_dict_field(
            usage_data,
            "cachedContentTokenCount",
            "cached_content_token_count",
        )),
        output_tokens_details=NS(reasoning_tokens=_gemini_dict_field(
            usage_data,
            "thoughtsTokenCount",
            "thoughts_token_count",
        )),
    )

    finish_reason = str(
        _gemini_dict_field(candidate, "finishReason", "finish_reason", default="")
    )
    completed = bool(candidate) and finish_reason in {
        "",
        "STOP",
        "FINISH_REASON_UNSPECIFIED",
    }
    response = NS(
        status="completed" if completed else "incomplete",
        output_text="".join(final_text_pieces),
        output=output,
        usage=usage,
    )
    response._hina_web_search_calls = web_search_calls
    response._hina_error_codes = (
        [] if completed or not finish_reason else [finish_reason]
    )
    return response



def _citation_label(url: str) -> str:
    parsed = urlsplit(url)
    host = parsed.netloc.removeprefix("www.")
    path = parsed.path.rstrip("/")
    return (host + path) if host else url


def _gemini_output(data: dict):
    output = []
    final_text_pieces = []
    previous_step_was_model_output = False
    web_search_calls = 0

    for step in data.get("steps") or []:
        step_type = step.get("type")
        if step_type != "model_output":
            previous_step_was_model_output = False
        if step_type == "google_search_call":
            output.append(NS(type="web_search_call"))
            web_search_calls += 1
            continue
        if step_type == "function_call":
            call_id = str(step.get("id") or "")
            name = str(step.get("name") or "")
            if call_id and name:
                output.append(NS(
                    type="function_call",
                    id=call_id,
                    call_id=call_id,
                    name=name,
                    arguments=json.dumps(
                        step.get("arguments") or {},
                        ensure_ascii=False,
                        separators=(",", ":"),
                    ),
                ))
            continue
        if step_type == "code_execution_call":
            output.append(NS(
                type="code_execution_call",
                id=str(step.get("id") or ""),
                arguments=step.get("arguments") or {},
            ))
            continue
        if step_type == "code_execution_result":
            output.append(NS(
                type="code_execution_result",
                call_id=str(step.get("call_id") or ""),
                result=step.get("result") or "",
                is_error=bool(step.get("is_error", False)),
            ))
            continue
        if step_type != "model_output":
            continue

        if not previous_step_was_model_output:
            final_text_pieces = []
        previous_step_was_model_output = True

        parts = []
        for block in step.get("content") or []:
            if block.get("type") != "text":
                continue
            text = block.get("text") or ""
            annotations = []
            seen_urls = set()
            for annotation in block.get("annotations") or []:
                if annotation.get("type") != "url_citation":
                    continue
                url = annotation.get("url")
                if not isinstance(url, str) or not url or url in seen_urls:
                    continue
                seen_urls.add(url)
                suffix = f" ({_citation_label(url)})"
                start = len(text)
                text += suffix
                annotations.append(NS(
                    type="url_citation",
                    start_index=start,
                    end_index=len(text),
                    url=url,
                    title=annotation.get("title") or "",
                ))
            final_text_pieces.append(text)
            parts.append(NS(type="output_text", text=text, annotations=annotations))
        if parts:
            output.append(NS(type="message", content=parts))

    usage_data = data.get("usage") or {}
    usage = NS(
        input_tokens=usage_data.get("total_input_tokens"),
        output_tokens=usage_data.get("total_output_tokens"),
        total_tokens=usage_data.get("total_tokens"),
        input_tokens_details=NS(cached_tokens=usage_data.get("total_cached_tokens")),
        output_tokens_details=NS(reasoning_tokens=usage_data.get("total_thought_tokens")),
    )
    response = NS(
        status=data.get("status", "completed"),
        output_text="".join(final_text_pieces),
        output=output,
        usage=usage,
    )
    response._hina_web_search_calls = web_search_calls
    response._hina_error_codes = [
        error.get("code") for error in data.get("errors") or []
        if isinstance(error, dict) and isinstance(error.get("code"), str)
    ]
    return response


class _GeminiResponses:
    def __init__(
        self,
        http: httpx.AsyncClient,
        *,
        thinking_level: str = "low",
    ):
        self.http = http
        self.thinking_level = thinking_level

    async def _generate_content(
        self,
        *,
        web_tools: list[dict],
        function_tools: list[dict],
        code_execution_tools: list[dict],
        **kwargs,
    ):
        model = str(kwargs["model"]).removeprefix("models/")
        thinking_level = kwargs.get("thinking_level", self.thinking_level)
        if thinking_level not in {"minimal", "low", "medium", "high"}:
            raise ValueError("Gemini thinking_level 값이 잘못되었습니다.")

        instructions = kwargs.get("instructions") or ""
        if web_tools and kwargs.get("tool_choice") == "required":
            guidance = (
                "이 요청은 외부 확인이 필수입니다. Google Search를 사용해 필요한 사실을 "
                "확인한 뒤, 충분한 근거를 얻으면 검색을 반복하지 말고 최종 답변을 작성하세요."
            )
            instructions = (instructions + "\n\n" + guidance).strip()

        payload = {
            "contents": _gemini_generate_content_contents(kwargs.get("input", "")),
            "store": bool(kwargs.get("store", False)),
            "generationConfig": {
                "thinkingConfig": {"thinkingLevel": thinking_level},
            },
        }
        if instructions:
            payload["system_instruction"] = {
                "parts": [{"text": instructions}],
            }

        max_output_tokens = kwargs.get("max_output_tokens")
        if isinstance(max_output_tokens, int):
            payload["generationConfig"]["maxOutputTokens"] = max_output_tokens

        payload_tools = []
        if web_tools:
            payload_tools.append({"google_search": {}})
        if function_tools:
            payload_tools.append({
                "function_declarations": [{
                    "name": tool["name"],
                    "description": tool.get("description", ""),
                    "parameters": _gemini_generate_content_schema(
                        tool.get("parameters")
                        or {"type": "object", "properties": {}}
                    ),
                } for tool in function_tools]
            })
        if code_execution_tools:
            payload_tools.append({"code_execution": {}})
        if payload_tools:
            payload["tools"] = payload_tools

        if web_tools and function_tools:
            payload["toolConfig"] = {
                "includeServerSideToolInvocations": True,
                "functionCallingConfig": {"mode": "VALIDATED"},
            }

        url = GEMINI_GENERATE_CONTENT_URL.format(model=model)
        response = await self.http.post(url, json=payload)
        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            error = _gemini_http_error(response)
            log.warning("Provider request failed (%s)", error.safe_diagnostic)
            raise error from exc
        return _gemini_generate_content_output(response.json())

    async def _interaction(
        self,
        *,
        function_tools: list[dict],
        code_execution_tools: list[dict],
        **kwargs,
    ):
        payload = {
            "model": kwargs["model"],
            "input": _gemini_input(kwargs.get("input", "")),
            "store": bool(kwargs.get("store", False)),
        }
        previous_interaction_id = kwargs.get("previous_interaction_id")
        if previous_interaction_id:
            payload["previous_interaction_id"] = str(previous_interaction_id)
        instructions = kwargs.get("instructions")
        if instructions:
            payload["system_instruction"] = instructions

        thinking_level = kwargs.get("thinking_level", self.thinking_level)
        if thinking_level not in {"minimal", "low", "medium", "high"}:
            raise ValueError("Gemini thinking_level 값이 잘못되었습니다.")
        generation_config = {"thinking_level": thinking_level}
        max_output_tokens = kwargs.get("max_output_tokens")
        if isinstance(max_output_tokens, int):
            generation_config["max_output_tokens"] = max_output_tokens

        payload_tools = []
        for tool in function_tools:
            payload_tools.append({
                "type": "function",
                "name": tool["name"],
                "description": tool.get("description", ""),
                "parameters": (
                    tool.get("parameters")
                    or {"type": "object", "properties": {}}
                ),
            })
        if code_execution_tools:
            payload_tools.append({"type": "code_execution"})
        if payload_tools:
            payload["tools"] = payload_tools

        payload["generation_config"] = generation_config
        response = await self.http.post(GEMINI_INTERACTIONS_URL, json=payload)
        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            error = _gemini_http_error(response)
            log.warning("Provider request failed (%s)", error.safe_diagnostic)
            raise error from exc
        data = response.json()
        normalized = _gemini_output(data)
        normalized._hina_interaction_id = (
            str(data.get("id") or "") if payload["store"] else ""
        )
        normalized._hina_interaction_steps = tuple(
            dict(step)
            for step in (data.get("steps") or ())
            if isinstance(step, dict)
        )
        return normalized

    async def create(self, **kwargs):
        tools = list(kwargs.get("tools") or [])
        unknown_tools = [
            tool for tool in tools
            if tool.get("type") not in {"web_search", "function", "code_execution"}
        ]
        if unknown_tools:
            raise ValueError("Gemini provider가 지원하지 않는 tool type입니다.")

        web_tools = [tool for tool in tools if tool.get("type") == "web_search"]
        function_tools = [tool for tool in tools if tool.get("type") == "function"]
        code_execution_tools = [
            tool for tool in tools if tool.get("type") == "code_execution"
        ]
        if web_tools:
            return await self._generate_content(
                web_tools=web_tools,
                function_tools=function_tools,
                code_execution_tools=code_execution_tools,
                **kwargs,
            )
        return await self._interaction(
            function_tools=function_tools,
            code_execution_tools=code_execution_tools,
            **kwargs,
        )


class GeminiClient:
    provider_name = "gemini"

    def __init__(
        self,
        credential: str,
        *,
        timeout: float = 45,
        thinking_level: str = "low",
    ):
        self._http = httpx.AsyncClient(
            timeout=timeout,
            headers={"x-goog-api-key": credential, "Content-Type": "application/json"},
        )
        self.responses = _GeminiResponses(
            self._http,
            thinking_level=thinking_level,
        )

    async def close(self):
        await self._http.aclose()


class _OpenRouterResponses:
    def __init__(self, responses):
        self._responses = responses

    async def create(self, **kwargs):
        request = dict(kwargs)
        tools = list(request.get("tools") or [])
        unknown_tools = [
            tool for tool in tools
            if tool.get("type") not in {"web_search", "function"}
        ]
        if unknown_tools:
            raise ValueError("OpenRouter provider가 지원하지 않는 tool type입니다.")

        web_tools = [tool for tool in tools if tool.get("type") == "web_search"]
        function_tools = [tool for tool in tools if tool.get("type") == "function"]
        if web_tools:
            extra_body = dict(request.pop("extra_body", {}) or {})
            plugins = list(extra_body.get("plugins") or [])
            plugins.append({"id": "web", "max_results": 3})
            extra_body["plugins"] = plugins
            request["extra_body"] = extra_body
            if function_tools:
                request["tools"] = function_tools
                # Mixed local/server tools must remain model-selectable. Search-required policy
                # continues to be expressed by the caller's instruction layer.
                request["tool_choice"] = "auto"
            else:
                request.pop("tools", None)
                request.pop("tool_choice", None)
        return await self._responses.create(**request)


class OpenRouterClient:
    provider_name = "openrouter"

    def __init__(self, credential: str, *, timeout: float = 45, max_retries: int = 2):
        self._client = AsyncOpenAI(
            api_key=credential,
            base_url=OPENROUTER_BASE_URL,
            timeout=timeout,
            max_retries=max_retries,
        )
        self.responses = _OpenRouterResponses(self._client.responses)

    async def close(self):
        await self._client.close()


def create_provider_client(
    settings,
    provider: str,
    *,
    credential: str | None = None,
    timeout: float = 45,
    max_retries: int = 2,
    thinking_level: str | None = None,
):
    provider = normalize_provider(provider)
    credential = credential if credential is not None else settings.api_key_for(provider)
    if not credential:
        raise ValueError(f"{provider} provider API key가 설정되지 않았습니다.")
    if provider == "gemini":
        return GeminiClient(
            credential,
            timeout=timeout,
            thinking_level=thinking_level or settings.gemini_thinking_level,
        )
    if provider == "openrouter":
        return OpenRouterClient(credential, timeout=timeout, max_retries=max_retries)
    return AsyncOpenAI(api_key=credential, timeout=timeout, max_retries=max_retries)

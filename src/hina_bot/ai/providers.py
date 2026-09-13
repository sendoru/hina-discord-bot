"""Model-provider adapters for the bot runtime."""

import json
import logging
from types import SimpleNamespace as NS
from urllib.parse import urlsplit

import httpx
from openai import AsyncOpenAI

GEMINI_INTERACTIONS_URL = "https://generativelanguage.googleapis.com/v1beta/interactions"
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


def _text_content(value) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        pieces = []
        for item in value:
            if isinstance(item, str):
                pieces.append(item)
            elif isinstance(item, dict):
                text = item.get("text")
                if isinstance(text, str):
                    pieces.append(text)
        return "\n".join(pieces)
    return str(value)


def _gemini_input(value):
    if isinstance(value, str):
        return value
    if not isinstance(value, list):
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))

    steps = []
    for item in value:
        if not isinstance(item, dict):
            continue
        role = item.get("role", "user")
        text = _text_content(item.get("content", ""))
        if not text:
            continue
        step_type = "model_output" if role == "assistant" else "user_input"
        steps.append({"type": step_type, "content": [{"type": "text", "text": text}]})
    return steps


def _citation_label(url: str) -> str:
    parsed = urlsplit(url)
    host = parsed.netloc.removeprefix("www.")
    path = parsed.path.rstrip("/")
    return (host + path) if host else url


def _gemini_output(data: dict):
    output = []
    text_pieces = []
    web_search_calls = 0

    for step in data.get("steps") or []:
        step_type = step.get("type")
        if step_type == "google_search_call":
            output.append(NS(type="web_search_call"))
            web_search_calls += 1
            continue
        if step_type != "model_output":
            continue

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
            text_pieces.append(text)
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
        output_text="".join(text_pieces),
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
    def __init__(self, http: httpx.AsyncClient, *, thinking_level: str = "low",
                 total_output_tokens: int = 4096):
        self.http = http
        self.thinking_level = thinking_level
        self.total_output_tokens = total_output_tokens

    async def create(self, **kwargs):
        payload = {
            "model": kwargs["model"],
            "input": _gemini_input(kwargs.get("input", "")),
            "store": bool(kwargs.get("store", False)),
        }
        instructions = kwargs.get("instructions")
        if instructions:
            payload["system_instruction"] = instructions

        generation_config = {"thinking_level": self.thinking_level}
        max_output_tokens = kwargs.get("max_output_tokens")
        if isinstance(max_output_tokens, int):
            # Gemini counts hidden thought tokens against max_output_tokens. Keep a separate
            # provider budget so a short visible-answer limit does not cut reasoning off first.
            generation_config["max_output_tokens"] = max(
                max_output_tokens, self.total_output_tokens)

        tools = kwargs.get("tools") or []
        unknown_tools = [tool for tool in tools if tool.get("type") != "web_search"]
        if unknown_tools:
            raise ValueError("Gemini provider는 현재 web_search 서버 도구만 변환합니다.")

        required_search = bool(tools and kwargs.get("tool_choice") == "required")
        if tools:
            payload["tools"] = [{"type": "google_search", "search_types": ["web_search"]}]
            if required_search:
                # OpenAI `required` means that a tool must be used before the final answer.
                # Gemini `any` is stronger: every model step must be a tool call. With a
                # server-side built-in search this can loop until Gemini rejects the request as
                # "Model generated too many tool calls". Keep Gemini in auto mode and express
                # the one-search requirement in the system instruction instead.
                generation_config["tool_choice"] = "auto"
                guidance = (
                    "이 요청은 외부 확인이 필수입니다. Google Search를 사용해 필요한 사실을 "
                    "확인한 뒤, 충분한 근거를 얻으면 검색을 반복하지 말고 최종 답변을 작성하세요."
                )
                payload["system_instruction"] = (
                    (payload.get("system_instruction") or "") + "\n\n" + guidance
                ).strip()

        payload["generation_config"] = generation_config

        response = await self.http.post(GEMINI_INTERACTIONS_URL, json=payload)
        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            error = _gemini_http_error(response)
            too_many_calls = (
                bool(tools)
                and error.status_code == 400
                and "too many tool calls" in error.error_message.lower()
            )
            if too_many_calls:
                # Gemini recommends retrying this specific failure with the error included in
                # the prompt. Retry once with a strict single-search bound.
                retry_payload = dict(payload)
                retry_payload["generation_config"] = dict(generation_config)
                retry_note = (
                    "이전 시도에서 'Model generated too many tool calls.' 오류가 발생했습니다. "
                    "Google Search 호출은 최대 1회만 사용하세요. 확인할 검색어가 여러 개면 한 "
                    "호출에 묶고, 검색 결과를 확인한 뒤 반드시 최종 답변을 작성하세요."
                )
                retry_payload["system_instruction"] = (
                    (payload.get("system_instruction") or "") + "\n\n" + retry_note
                ).strip()
                retry = await self.http.post(GEMINI_INTERACTIONS_URL, json=retry_payload)
                try:
                    retry.raise_for_status()
                except httpx.HTTPStatusError as retry_exc:
                    retry_error = _gemini_http_error(retry)
                    log.warning("Provider request failed (%s)", retry_error.safe_diagnostic)
                    raise retry_error from retry_exc
                return _gemini_output(retry.json())
            log.warning("Provider request failed (%s)", error.safe_diagnostic)
            raise error from exc
        return _gemini_output(response.json())


class GeminiClient:
    provider_name = "gemini"

    def __init__(self, credential: str, *, timeout: float = 45,
                 thinking_level: str = "low", total_output_tokens: int = 4096):
        self._http = httpx.AsyncClient(
            timeout=timeout,
            headers={"x-goog-api-key": credential, "Content-Type": "application/json"},
        )
        self.responses = _GeminiResponses(
            self._http,
            thinking_level=thinking_level,
            total_output_tokens=total_output_tokens,
        )

    async def close(self):
        await self._http.aclose()


class _OpenRouterResponses:
    def __init__(self, responses):
        self._responses = responses

    async def create(self, **kwargs):
        request = dict(kwargs)
        tools = request.get("tools") or []
        if any(tool.get("type") == "web_search" for tool in tools):
            if any(tool.get("type") != "web_search" for tool in tools):
                raise ValueError("OpenRouter provider는 현재 web_search 서버 도구만 변환합니다.")
            request.pop("tools", None)
            request.pop("tool_choice", None)
            extra_body = dict(request.pop("extra_body", {}) or {})
            plugins = list(extra_body.get("plugins") or [])
            plugins.append({"id": "web", "max_results": 3})
            extra_body["plugins"] = plugins
            request["extra_body"] = extra_body
        return await self._responses.create(**request)


class OpenRouterClient:
    provider_name = "openrouter"

    def __init__(self, credential: str, *, timeout: float = 45):
        self._client = AsyncOpenAI(
            api_key=credential,
            base_url=OPENROUTER_BASE_URL,
            timeout=timeout,
            max_retries=2,
        )
        self.responses = _OpenRouterResponses(self._client.responses)

    async def close(self):
        await self._client.close()


def create_provider_client(settings, provider: str):
    provider = normalize_provider(provider)
    credential = settings.api_key_for(provider)
    if not credential:
        raise ValueError(f"{provider} provider API key가 설정되지 않았습니다.")
    if provider == "gemini":
        return GeminiClient(
            credential,
            thinking_level=settings.gemini_thinking_level,
            total_output_tokens=settings.gemini_total_output_tokens,
        )
    if provider == "openrouter":
        return OpenRouterClient(credential)
    return AsyncOpenAI(api_key=credential, timeout=45, max_retries=2)

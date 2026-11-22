"""Opt-in, low-cost live compatibility probe for Gemini native audio + code execution.

This is a diagnostic command, not a bot runtime feature. It uses the production
Gemini provider adapter, without the request assembler's audio/code gating.
Audio and API keys are never printed or stored.
"""

from __future__ import annotations

import argparse
import asyncio
import io
import os
import time
import wave
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace

import httpx
from dotenv import load_dotenv

from hina_bot.ai.providers import GeminiClient, ProviderAPIError
from hina_bot.discord.audio import _audio_duration, _sniff_audio

_MAX_AUDIO_BYTES = 4 * 1024 * 1024
_MAX_SECONDS = 60
_CODE = {"type": "code_execution"}
_FUNCTION = {
    "type": "function",
    "name": "calculator_probe",
    "description": "Returns the product of two integers.",
    "parameters": {
        "type": "object",
        "properties": {"a": {"type": "integer"}, "b": {"type": "integer"}},
        "required": ["a", "b"],
    },
}


@dataclass(frozen=True)
class ProbeCase:
    api: str
    variant: str
    audio: bool
    tools: tuple[dict, ...]


def cases(*, apis=("interactions", "generate_content"), include_function=False,
          include_search=False) -> list[ProbeCase]:
    """Text/code is the positive control; audio/no-tool is the audio control."""
    result = []
    for api in apis:
        result.extend((
            ProbeCase(api, "text+code", False, (_CODE,)),
            ProbeCase(api, "audio-only", True, ()),
            ProbeCase(api, "audio+code", True, (_CODE,)),
        ))
        if include_function:
            result.append(ProbeCase(api, "audio+function", True, (_FUNCTION,)))
        if include_search:
            result.append(ProbeCase(api, "audio+search", True, ({"type": "web_search"},)))
    return result


def _silence_wav() -> tuple[bytes, str, str]:
    """Small, known-good one-second PCM file for a default baseline."""
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(8000)
        wav.writeframes(b"\x00\x00" * 8000)
    return buf.getvalue(), "audio/wav", "generated-1s-wav"


def audio_sample(path: str | None, *, max_seconds: float = 10) -> tuple[bytes, str, str]:
    if not path:
        return _silence_wav()
    source = Path(path)
    if not source.is_file() or source.stat().st_size > _MAX_AUDIO_BYTES:
        raise ValueError("Audio must be a file of at most 4 MiB")
    data = source.read_bytes()
    mime = _sniff_audio(data)
    if mime is None:
        raise ValueError("Unsupported or unrecognized audio container")
    duration = _audio_duration(data, mime)
    if duration is None:
        raise ValueError("Cannot verify audio duration; use a short WAV/OGG/MP3/FLAC/M4A")
    if not 0 < duration <= max_seconds:
        raise ValueError(f"Audio duration must be >0 and at most {max_seconds:g} seconds")
    # Avoid printing paths, filenames, or any raw media.
    return data, mime, f"local-{mime}-{duration:.1f}s"


def _input(case: ProbeCase, *, data: bytes, mime: str) -> str | list[dict]:
    prompt = (
        "Listen to the audio, briefly describe the sounds. Also calculate 17*19."
        if case.audio else
        "Calculate 17*19. When code execution is available, use it."
    )
    if not case.audio:
        return prompt
    import base64

    encoded = base64.b64encode(data).decode("ascii")
    return [{"role": "user", "content": [
        {"type": "input_text", "text": prompt},
        {"type": "input_audio", "audio_url": f"data:{mime};base64,{encoded}"},
    ]}]


async def run_probe(*, model: str, data: bytes, mime: str,
                    selected_cases: list[ProbeCase], timeout: float = 30.0) -> None:
    settings = SimpleNamespace(
        gemini_store_interactions=False,
        gemini_search_api_mode="interactions",
    )
    client = GeminiClient(
        os.environ["GEMINI_API_KEY"], timeout=timeout, thinking_level="minimal",
        settings=settings,
    )
    print("API | variant | HTTP | result | elapsed_ms")
    try:
        for case in selected_cases:
            started = time.perf_counter()
            status_code = "?"
            result = ""
            try:
                response = await client.responses.create(
                    model=model,
                    input=_input(case, data=data, mime=mime),
                    tools=list(case.tools),
                    store=False,
                    thinking_level="minimal",
                    max_output_tokens=128,
                    _hina_gemini_api=case.api,  # Disable API fallback: exact endpoint under test
                )
                status_code = "200"
                result = str(getattr(response, "status", "completed"))
                if result == "requires_action":
                    result = "requires_action (API accepted)"
            except ProviderAPIError as exc:
                status_code = str(exc.status_code)
                # Provider validation errors only; never print audio, prompts, or keys.
                result = f"{exc.error_code}: {exc.error_message}"[:200]
            except (httpx.TimeoutException, httpx.TransportError) as exc:
                result = f"network_{type(exc).__name__}"
            duration_ms = round((time.perf_counter() - started) * 1000)
            print(f"{case.api} | {case.variant} | {status_code} | {result} | {duration_ms}")
    finally:
        await client.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Probe Gemini audio/code-execution API compatibility without changing bot settings"
    )
    parser.add_argument("--audio", help="Short local audio file, default: generated 1s WAV")
    parser.add_argument("--model", default="", help="Model ID; defaults to LLM_MODEL env")
    parser.add_argument("--api", choices=("both", "interactions", "generate_content"),
                        default="both")
    parser.add_argument("--max-seconds", type=float, default=10,
                        help="Maximum sample duration, default 10 seconds, hard cap 60")
    parser.add_argument("--include-function", action="store_true")
    parser.add_argument("--include-search", action="store_true",
                        help="Adds Google Search tests; may incur search charges")
    parser.add_argument("--dry-run", action="store_true",
                        help="Show case matrix without sending requests")
    args = parser.parse_args(argv)

    if not 0 < args.max_seconds <= _MAX_SECONDS:
        parser.error("--max-seconds must be between 0 and 60")
    apis = ("interactions", "generate_content") if args.api == "both" else (args.api,)
    selected_cases = cases(
        apis=apis, include_function=args.include_function,
        include_search=args.include_search,
    )
    if args.dry_run:
        for case in selected_cases:
            print(f"{case.api} | {case.variant}")
        return 0

    load_dotenv(".env.local", override=False)
    load_dotenv(".env", override=False)
    if not os.environ.get("GEMINI_API_KEY"):
        parser.error("GEMINI_API_KEY is missing; supply it using your existing local env")
    model = args.model or os.environ.get("LLM_MODEL") or "gemini-3.5-flash-lite"
    try:
        data, mime, name = audio_sample(args.audio, max_seconds=args.max_seconds)
    except (OSError, ValueError) as exc:
        parser.error(str(exc))
    print(f"Model={model}; sample={name}; requests={len(selected_cases)}")
    print("Responses report API validation only; 200 does not mean the model used the tool.")
    asyncio.run(run_probe(model=model, data=data, mime=mime, selected_cases=selected_cases))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

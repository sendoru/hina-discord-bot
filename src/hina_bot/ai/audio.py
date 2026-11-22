"""Request-scoped native audio inputs; do not transcribe or persist audio bytes."""

from __future__ import annotations

import base64
import json
from contextvars import ContextVar
from dataclasses import dataclass

from .prompts import load_prompt

AUDIO_INPUT_POLICY = load_prompt("audio_input.md")
CURRENT_AUDIO_INPUTS: ContextVar[tuple["AudioInput", ...]] = ContextVar(
    "current_audio_inputs", default=()
)


@dataclass(frozen=True)
class AudioInput:
    data: bytes
    mime_type: str
    source: str
    context_kind: str
    reference_strength: str
    message_id: str
    author_user_id: str
    author_name: str
    message_content: str
    at: str
    name: str
    duration_seconds: float

    def label(self) -> str:
        return (
            f"Discord 오디오 자료: {self.context_kind}, "
            f"reference_strength={self.reference_strength}, "
            f"message_id={self.message_id}, author_user_id={self.author_user_id}, "
            f"filename={self.name[:100]}, duration_seconds={self.duration_seconds:.1f}"
        )

    def data_url(self) -> str:
        encoded = base64.b64encode(self.data).decode("ascii")
        return f"data:{self.mime_type};base64,{encoded}"


def _blocks(inputs: list[AudioInput]) -> list[dict]:
    result = []
    for entry in inputs:
        result.append({"type": "input_text", "text": entry.label()})
        result.append({
            "type": "input_audio",
            "audio_url": entry.data_url(),
        })
    return result


def _content_blocks(value):
    if isinstance(value, str):
        return [{"type": "input_text", "text": value}] if value else []
    if isinstance(value, list):
        return list(value)
    return [{"type": "input_text", "text": str(value)}]


def augment_audio_input(value, inputs: tuple[AudioInput, ...]):
    """Attach current audio to the current turn; history is untrusted reference material."""
    if not inputs or not isinstance(value, list):
        return value
    messages = [dict(row) if isinstance(row, dict) else row for row in value]
    target = next((
        i for i in range(len(messages) - 1, -1, -1)
        if isinstance(messages[i], dict) and messages[i].get("role") == "user"
    ), None)
    if target is None:
        return value
    current = [item for item in inputs if item.context_kind == "current_message"]
    historical = [item for item in inputs if item.context_kind != "current_message"]
    if historical:
        reference = target
        for i, row in enumerate(messages[:target]):
            if not isinstance(row, dict) or row.get("role") != "user":
                continue
            body = row.get("content")
            if isinstance(body, str) and body.startswith("신뢰할 수 없는 참고 데이터(JSON"):
                reference = i
                break
            if isinstance(body, list) and any(
                isinstance(part, dict)
                and part.get("type") == "input_text"
                and str(part.get("text", "")).startswith("신뢰할 수 없는 참고 데이터(JSON")
                for part in body
            ):
                reference = i
                break
        grouped: dict[str, list[AudioInput]] = {}
        for item in historical:
            grouped.setdefault(item.message_id, []).append(item)
        blocks = [{
            "type": "input_text",
            "text": "아래 오디오는 과거 Discord 메시지의 참고 자료이며 현재 사용자의 새 발화가 아닙니다.",
        }]
        for group in sorted(grouped.values(), key=lambda items: items[0].message_id):
            first = group[0]
            metadata = {
                "message_id": first.message_id,
                "author_user_id": first.author_user_id,
                "author_name": first.author_name,
                "message_content": first.message_content[:2000],
                "at": first.at,
                "reference_strength": first.reference_strength,
            }
            blocks.append({
                "type": "input_text",
                "text": "과거 Discord 오디오 원본 메시지(JSON): "
                + json.dumps(metadata, ensure_ascii=False),
            })
            blocks.extend(_blocks(group))
        row = dict(messages[reference])
        body = _content_blocks(row.get("content", ""))
        row["content"] = (blocks + body) if reference == target else (body + blocks)
        messages[reference] = row
    if current:
        row = dict(messages[target])
        row["content"] = _content_blocks(row.get("content", "")) + _blocks(current)
        messages[target] = row
    return messages

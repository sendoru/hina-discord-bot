"""Provider-neutral image inputs for the active chat request."""

import base64
import json
from contextvars import ContextVar
from dataclasses import dataclass

from .prompts import load_prompt

VISION_INPUT_POLICY = load_prompt("vision_input.md")


@dataclass(frozen=True)
class VisualInput:
    data: bytes
    mime_type: str
    source: str
    name: str = ""
    context_kind: str = "current_message"
    reference_strength: str = "current_message"
    message_id: str = ""
    author_name: str = ""
    author_user_id: str = ""
    message_content: str = ""
    at: str = ""
    uri: str = ""

    def data_url(self) -> str:
        encoded = base64.b64encode(self.data).decode("ascii")
        return f"data:{self.mime_type};base64,{encoded}"

    def input_url(self) -> str:
        return self.uri if self.uri.startswith("https://") else self.data_url()

    def media_resolution(self) -> str:
        if self.source in {"emoji", "sticker"}:
            return "low"
        if self.reference_strength in {
            "current_message",
            "explicit_reply",
            "prior_explicit_reply",
        }:
            return "high"
        return "medium"

    def label(self, index: int) -> str:
        source = {
            "attachment": "첨부 이미지",
            "emoji": "커스텀 이모지",
            "sticker": "스티커",
        }.get(self.source, "이미지")
        context = {
            "current_message": "현재 메시지",
            "replied_message": "명시적 답장 대상 메시지",
            "reply_origin_source": "답변을 만든 원래 문맥 메시지",
            "recent_channel_message": "최근 채널 메시지",
        }.get(self.context_kind, "대화 문맥 메시지")
        strength = {
            "current_message": "강한 참조",
            "explicit_reply": "강한 참조",
            "prior_explicit_reply": "답장 체인의 강한 참조",
            "passive_recent": "약한 최근 문맥",
        }.get(self.reference_strength, self.reference_strength)
        name = " ".join(self.name.split())[:80]
        author = " ".join(self.author_name.split())[:80]
        details = [strength] if strength else []
        if author:
            details.append(f"작성자 {author}")
        if self.message_id:
            details.append(f"message_id {self.message_id}")
        suffix = f" · {' · '.join(details)}" if details else ""
        if name:
            suffix += f" · {name}"
        return f"[{context}의 {source} {index}{suffix}]"


CURRENT_VISUAL_INPUTS: ContextVar[tuple[VisualInput, ...]] = ContextVar(
    "current_visual_inputs", default=()
)
VISION_REQUEST_ACTIVE: ContextVar[bool] = ContextVar("vision_request_active", default=False)


def _visual_blocks(
    visuals: list[VisualInput],
    *,
    include_media_resolution: bool = False,
) -> list[dict]:
    blocks = []
    for index, visual in enumerate(visuals, 1):
        blocks.append({"type": "input_text", "text": visual.label(index)})
        image = {
            "type": "input_image",
            "image_url": visual.input_url(),
            "detail": "auto",
        }
        if include_media_resolution:
            image["resolution"] = visual.media_resolution()
        blocks.append(image)
    return blocks


def _message_order(message_id: str) -> tuple[int, int | str]:
    try:
        return 0, int(message_id)
    except (TypeError, ValueError):
        return 1, message_id


def _historical_visual_messages(
    visuals: list[VisualInput],
    *,
    include_media_resolution: bool = False,
) -> list[dict]:
    grouped: dict[str, list[VisualInput]] = {}
    for index, visual in enumerate(visuals):
        key = visual.message_id or f"missing:{index}"
        grouped.setdefault(key, []).append(visual)

    messages = []
    for _, group in sorted(
        grouped.items(),
        key=lambda item: _message_order(item[1][0].message_id),
    ):
        first = group[0]
        metadata = {
            "context_kind": first.context_kind,
            "reference_strength": first.reference_strength,
            "message_id": first.message_id,
            "author_name": first.author_name,
            "author_user_id": first.author_user_id,
            "message_content": first.message_content,
            "at": first.at,
        }
        content = [{
            "type": "input_text",
            "text": (
                "신뢰할 수 없는 과거 Discord 시각 문맥(JSON):\n"
                + json.dumps(metadata, ensure_ascii=False, separators=(",", ":"))
            ),
        }]
        content.extend(
            _visual_blocks(group, include_media_resolution=include_media_resolution)
        )
        messages.append({"role": "user", "content": content})
    return messages


def _augment_input(
    input_value,
    visuals: tuple[VisualInput, ...],
    *,
    include_media_resolution: bool = False,
):
    if not isinstance(input_value, list):
        return input_value

    items = [dict(item) if isinstance(item, dict) else item for item in input_value]
    target = None
    for index in range(len(items) - 1, -1, -1):
        item = items[index]
        if isinstance(item, dict) and item.get("role") == "user":
            target = index
            break
    if target is None:
        return input_value

    current = [visual for visual in visuals if visual.context_kind == "current_message"]
    historical = [visual for visual in visuals if visual.context_kind != "current_message"]

    if historical:
        historical_messages = _historical_visual_messages(
            historical,
            include_media_resolution=include_media_resolution,
        )
        insertion = target
        for index, candidate in enumerate(items[:target]):
            if (
                isinstance(candidate, dict)
                and candidate.get("role") == "user"
                and isinstance(candidate.get("content"), str)
                and candidate["content"].startswith("신뢰할 수 없는 참고 데이터(JSON):")
            ):
                insertion = index
                break
        items[insertion:insertion] = historical_messages
        target += len(historical_messages)

    if not current:
        return items

    item = dict(items[target])
    original = item.get("content", "")
    if isinstance(original, str):
        content = ([{"type": "input_text", "text": original}] if original else [])
    elif isinstance(original, list):
        content = list(original)
    else:
        content = [{"type": "input_text", "text": str(original)}]

    content.extend(
        _visual_blocks(current, include_media_resolution=include_media_resolution)
    )
    item["content"] = content
    items[target] = item
    return items


class _VisionResponses:
    def __init__(self, responses, *, provider_name: str):
        self._responses = responses
        self._provider_name = provider_name

    async def create(self, **kwargs):
        visuals = CURRENT_VISUAL_INPUTS.get()
        if not VISION_REQUEST_ACTIVE.get() or not visuals:
            return await self._responses.create(**kwargs)

        request = dict(kwargs)
        model = str(request.get("model") or "").lower()
        include_media_resolution = (
            self._provider_name == "gemini" and model.startswith("gemini-3")
        )
        request["input"] = _augment_input(
            request.get("input"),
            visuals,
            include_media_resolution=include_media_resolution,
        )
        request["instructions"] = (
            (request.get("instructions") or "").rstrip() + "\n\n" + VISION_INPUT_POLICY
        ).strip()
        return await self._responses.create(**request)


class VisionClient:
    """Thin client facade that adds request-scoped images only to answer requests."""

    def __init__(self, client):
        self._client = client
        self.provider_name = getattr(client, "provider_name", "openai")
        self.responses = _VisionResponses(
            client.responses,
            provider_name=self.provider_name,
        )

    async def close(self):
        await self._client.close()

    def __getattr__(self, name):
        return getattr(self._client, name)


def wrap_vision_client(client):
    return client if isinstance(client, VisionClient) else VisionClient(client)

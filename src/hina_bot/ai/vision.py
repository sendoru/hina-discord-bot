"""Provider-neutral image inputs for the active chat request."""

import base64
import json
import logging
from contextvars import ContextVar
from dataclasses import dataclass
from pathlib import Path

from .discord_emoji_input import normalize_custom_emoji_text
from .prompts import load_prompt

VISION_INPUT_POLICY = load_prompt("vision_input.md")
log = logging.getLogger(__name__)

# A transport label, not another character-identification policy.
HINA_VISUAL_REFERENCE_LABEL = (
    "개발자가 제공한 소라사키 히나의 공식 외형 참조 이미지입니다. "
    "사용자가 보낸 이미지나 Discord 대화 기록이 아니며, "
    "사용자 이미지의 인물을 판별할 때 외형 비교 자료로만 사용하세요."
)
_MAX_HINA_REFERENCE_BYTES = 5 * 1024 * 1024
_MAX_HINA_REFERENCE_TOTAL_BYTES = 12 * 1024 * 1024
_MAX_HINA_REFERENCE_IMAGES = 4
_HINA_REFERENCE_SUFFIXES = frozenset({".png", ".jpg", ".jpeg", ".webp"})


def _load_hina_reference_image(path: Path) -> tuple[str, int] | None:
    """Read a validated operator-provided image at startup."""
    try:
        if path.stat().st_size > _MAX_HINA_REFERENCE_BYTES:
            log.warning("Hina visual reference ignored (over 5 MiB)")
            return None
        image = path.read_bytes()
    except OSError as exc:
        log.warning("Hina visual reference unavailable (%s)", type(exc).__name__)
        return None

    if not image or len(image) > _MAX_HINA_REFERENCE_BYTES:
        log.warning("Hina visual reference ignored (empty or over 5 MiB)")
        return None
    if image.startswith(b"\x89PNG\r\n\x1a\n"):
        mime_type = "image/png"
    elif image.startswith(b"\xff\xd8\xff"):
        mime_type = "image/jpeg"
    elif image.startswith(b"RIFF") and image[8:12] == b"WEBP":
        mime_type = "image/webp"
    else:
        log.warning("Hina visual reference ignored (unsupported image signature)")
        return None

    encoded = base64.b64encode(image).decode("ascii")
    return f"data:{mime_type};base64,{encoded}", len(image)


def _load_hina_reference_images(path: str) -> tuple[str, ...]:
    """Accept one image or a directory, with stable ordering and size limits."""
    if not path:
        return ()
    source = Path(path)
    try:
        if source.is_dir():
            candidates = sorted(
                (
                    item for item in source.iterdir()
                    if item.is_file() and item.suffix.lower() in _HINA_REFERENCE_SUFFIXES
                ),
                key=lambda item: (item.name.casefold(), item.name),
            )
        else:
            candidates = [source]
    except OSError as exc:
        log.warning("Hina visual reference directory unavailable (%s)", type(exc).__name__)
        return ()

    images: list[str] = []
    used_bytes = 0
    for candidate in candidates:
        if len(images) >= _MAX_HINA_REFERENCE_IMAGES:
            break
        loaded = _load_hina_reference_image(candidate)
        if loaded is None:
            continue
        image_url, image_bytes = loaded
        if used_bytes + image_bytes > _MAX_HINA_REFERENCE_TOTAL_BYTES:
            log.warning("Hina visual reference ignored (total exceeds 12 MiB)")
            continue
        images.append(image_url)
        used_bytes += image_bytes
    if images:
        log.info("Hina visual references ready (count=%d)", len(images))
    return tuple(images)


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
        return f"{context}의 {source} {index}{suffix}"


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


def _historical_visual_blocks(
    visuals: list[VisualInput],
    *,
    include_media_resolution: bool = False,
) -> list[dict]:
    """Attach historical images to reference data, not as new conversation turns."""
    grouped: dict[str, list[VisualInput]] = {}
    for index, visual in enumerate(visuals):
        key = visual.message_id or f"missing:{index}"
        grouped.setdefault(key, []).append(visual)

    blocks = [{
        "type": "input_text",
        "text": (
            "다음 시각 자료는 앞선 참고 데이터에 기록된 과거 Discord 메시지에 "
            "속합니다. 현재 사용자의 새 발화가 아니며, 이미지 자체만으로 "
            "히나의 실제 모습이나 경험을 증명하지 않습니다."
        ),
    }]
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
            "message_content": normalize_custom_emoji_text(first.message_content),
            "at": first.at,
        }
        blocks.append({
            "type": "input_text",
            "text": (
                "과거 Discord 시각 자료의 원본 메시지(JSON):\n"
                + json.dumps(metadata, ensure_ascii=False, separators=(",", ":"))
            ),
        })
        blocks.extend(
            _visual_blocks(group, include_media_resolution=include_media_resolution)
        )
    return blocks


def _content_blocks(original) -> list[dict]:
    if isinstance(original, str):
        return [{"type": "input_text", "text": original}] if original else []
    if isinstance(original, list):
        return list(original)
    return [{"type": "input_text", "text": str(original)}]


def _with_hina_visual_reference(
    input_value,
    image_urls: tuple[str, ...],
    *,
    include_media_resolution: bool,
):
    """Prepend fixed visual context without a synthetic Discord turn."""
    if not isinstance(input_value, list):
        return input_value
    index = next(
        (
            index for index, item in enumerate(input_value)
            if isinstance(item, dict) and item.get("role") == "user"
        ),
        None,
    )
    if index is None:
        return input_value

    items = list(input_value)
    item = dict(items[index])
    reference_blocks = [{"type": "input_text", "text": HINA_VISUAL_REFERENCE_LABEL}]
    for image_url in image_urls:
        image = {"type": "input_image", "image_url": image_url, "detail": "auto"}
        if include_media_resolution:
            image["resolution"] = "medium"
        reference_blocks.append(image)
    item["content"] = reference_blocks + _content_blocks(item.get("content", ""))
    items[index] = item
    return items


def _is_reference_content(content) -> bool:
    """Recognize either legacy string or partitioned reference input blocks."""
    if isinstance(content, str):
        return content.startswith("신뢰할 수 없는 참고 데이터(JSON")
    return (
        isinstance(content, list)
        and any(
            isinstance(block, dict)
            and block.get("type") == "input_text"
            and str(block.get("text") or "").startswith("신뢰할 수 없는 참고 데이터(JSON")
            for block in content
        )
    )


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
        # The reference JSON is already a user-role transport envelope. Keep historical
        # images with that envelope instead of inventing additional user-role turns that
        # the model could mistake for direct participation in the conversation.
        reference_index = target
        for index, candidate in enumerate(items[:target]):
            if (
                isinstance(candidate, dict)
                and candidate.get("role") == "user"
                and _is_reference_content(candidate.get("content"))
            ):
                reference_index = index
                break

        reference_item = dict(items[reference_index])
        reference_content = _content_blocks(reference_item.get("content", ""))
        historical_blocks = _historical_visual_blocks(
            historical,
            include_media_resolution=include_media_resolution,
        )
        if reference_index == target:
            # Fallback for callers without a separate reference envelope: leave the
            # current user's actual text after all historical visual reference blocks.
            reference_content = historical_blocks + reference_content
        else:
            reference_content.extend(historical_blocks)
        reference_item["content"] = reference_content
        items[reference_index] = reference_item

    if not current:
        return items

    item = dict(items[target])
    content = _content_blocks(item.get("content", ""))
    content.extend(
        _visual_blocks(current, include_media_resolution=include_media_resolution)
    )
    item["content"] = content
    items[target] = item
    return items

class _VisionResponses:
    def __init__(
        self, responses, *, provider_name: str,
        reference_image_urls: tuple[str, ...] = (),
    ):
        self._responses = responses
        self._provider_name = provider_name
        self._reference_image_urls = reference_image_urls

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
        # Passive recent images alone do not activate the fixed reference.
        if (
            self._provider_name == "gemini"
            and self._reference_image_urls
            and any(
                visual.context_kind == "current_message"
                or visual.reference_strength in {"explicit_reply", "prior_explicit_reply"}
                for visual in visuals
            )
        ):
            request["input"] = _with_hina_visual_reference(
                request["input"],
                self._reference_image_urls,
                include_media_resolution=include_media_resolution,
            )
            log.info(
                "vision.hina_visual_reference applied (count=%d)",
                len(self._reference_image_urls),
            )
        request["instructions"] = (
            (request.get("instructions") or "").rstrip() + "\n\n" + VISION_INPUT_POLICY
        ).strip()
        return await self._responses.create(**request)


class VisionClient:
    """Thin client facade that adds request-scoped images only to answer requests."""

    def __init__(self, client, *, reference_image_path: str = ""):
        self._client = client
        self.provider_name = getattr(client, "provider_name", "openai")
        reference_image_urls = (
            _load_hina_reference_images(reference_image_path)
            if self.provider_name == "gemini" else ()
        )
        self.responses = _VisionResponses(
            client.responses,
            provider_name=self.provider_name,
            reference_image_urls=reference_image_urls,
        )

    async def close(self):
        await self._client.close()

    def __getattr__(self, name):
        return getattr(self._client, name)


def wrap_vision_client(client, *, reference_image_path: str = ""):
    return (
        client if isinstance(client, VisionClient)
        else VisionClient(client, reference_image_path=reference_image_path)
    )

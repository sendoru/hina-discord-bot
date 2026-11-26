"""Public, model-scoped OpenRouter metadata. No prompts or credentials are sent."""

import asyncio
import json
import re
from time import monotonic

import httpx


_MODEL_SLUG = re.compile(
    r"[A-Za-z0-9_-][A-Za-z0-9._-]{0,127}/[A-Za-z0-9_-][A-Za-z0-9._-]{0,127}"
    r"(?::[A-Za-z0-9_-]{1,32})?"
)


def catalog_model_slug(model):
    """Only routing-only suffixes can safely share the base catalog record."""
    if not isinstance(model, str) or not _MODEL_SLUG.fullmatch(model):
        return None
    base, _, variant = model.partition(":")
    return base if variant in {"floor", "nitro", "exacto"} else model


def reject_model_router(model):
    # OpenRouter's own router namespace and preset/family aliases can pick a
    # different model, so they are excluded from explicit-model operation.
    if isinstance(model, str) and (
        model.lower().startswith("openrouter/") or "@" in model or "~" in model
    ):
        raise ValueError("OpenRouter automatic model routing and presets are unavailable")


def request_requirements(request):
    requirements = []
    if request.get("reasoning"):
        requirements.append("reasoning")
    if any(t.get("type") == "function" for t in request.get("tools", ())):
        requirements.append("tools")
    if (request.get("text") or {}).get("format", {}).get("type") == "json_schema":
        requirements.append("structured_outputs")
    return requirements


async def bounded_model(catalog, model):
    if catalog is None:
        return {}
    try:
        async with asyncio.timeout(3.5):
            return await catalog.model(model)
    except TimeoutError:
        return {}


def modality_support(data, modality):
    architecture = data.get("architecture")
    inputs = architecture.get("input_modalities") if isinstance(architecture, dict) else None
    return modality in inputs if isinstance(inputs, list) else None


def selected_provider(response):
    metadata = getattr(response, "openrouter_metadata", None)
    endpoints = metadata.get("endpoints") if isinstance(metadata, dict) else None
    available = endpoints.get("available") if isinstance(endpoints, dict) else None
    selected = [item for item in available if isinstance(item, dict) and item.get("selected") is True
                ] if isinstance(available, list) else []
    if len(selected) != 1:
        return None
    name = selected[0].get("provider")
    return name if isinstance(name, str) and name else None


async def continuation_preferences(catalog, model, preferences, provider_name):
    """Resolve the selected display name to one catalog tag, never guess a slug."""
    data = await bounded_model(catalog, model)
    endpoints = data.get("endpoints")
    matches = [endpoint for endpoint in endpoints if isinstance(endpoint, dict)
               and any(isinstance(endpoint.get(key), str)
                       and endpoint[key].casefold() == provider_name.casefold()
                       for key in ("provider_name", "tag"))
               ] if isinstance(endpoints, list) else []
    if len(matches) != 1:
        raise ValueError("OpenRouter continuation endpoint is unknown or ambiguous")
    tag = matches[0].get("tag")
    if not isinstance(tag, str) or not tag:
        raise ValueError("OpenRouter continuation endpoint tag is unavailable")
    # A base slug may match other variants/regions. Only pin if the catalog
    # resolves this tag to a single candidate endpoint.
    if any(endpoint is not matches[0] and isinstance(endpoint, dict)
           and (endpoint.get("tag") == tag or str(endpoint.get("tag") or "").startswith(tag + "/"))
           for endpoint in endpoints):
        raise ValueError("OpenRouter continuation endpoint tag is ambiguous")
    allowed = preferences.get("only", ())
    order = preferences.get("order", ()) if preferences.get("allow_fallbacks") is False else ()
    for restriction in (allowed, order):
        if restriction and not any(tag == name or tag.startswith(name + "/") for name in restriction):
            raise ValueError("OpenRouter continuation endpoint is no longer allowed")
    return {**preferences, "only": [tag], "order": [tag], "allow_fallbacks": False}


class EndpointCatalog:
    def __init__(self, http):
        self.http = http
        self.cache = {}
        self.lock = asyncio.Lock()

    async def _read(self, path, *, params=None):
        try:
            async with asyncio.timeout(1.5):
                async with self.http.stream("GET", path, params=params) as response:
                    response.raise_for_status()
                    body = bytearray()
                    async for chunk in response.aiter_bytes():
                        if len(body) + len(chunk) > 512_000:
                            return None
                        body.extend(chunk)
                    payload = json.loads(body)
                    return payload.get("data") if isinstance(payload, dict) else None
        except (httpx.HTTPError, TimeoutError, ValueError, RecursionError):
            return None

    async def model(self, model):
        model = catalog_model_slug(model)
        if model is None:
            return {}
        async with self.lock:
            entry = self.cache.get(model)
            if entry and entry[0] > monotonic():
                return entry[1]
            endpoints = await self._read(f"models/{model}/endpoints")
            data = dict(endpoints) if isinstance(endpoints, dict) and endpoints.get("id") == model else {}
            models = await self._read("models", params={"q": model})
            for record in models if isinstance(models, list) else ():
                if isinstance(record, dict) and record.get("id") == model:
                    for key in ("reasoning", "supported_parameters", "architecture"):
                        if key in record:
                            data.setdefault(key, record[key])
                    break
            if model not in self.cache and len(self.cache) >= 32:
                del self.cache[next(iter(self.cache))]
            self.cache[model] = (monotonic() + (600 if data else 60), data)
            return data


def parameter_support(record, parameter, only=()):
    endpoints = record.get("endpoints")
    if not isinstance(endpoints, list):
        return None
    states = []
    for endpoint in endpoints:
        if not isinstance(endpoint, dict):
            return None
        tag = str(endpoint.get("tag") or "")
        if only and not any(tag == name or tag.startswith(name + "/") for name in only):
            continue
        params = endpoint.get("supported_parameters")
        states.append(parameter in params if isinstance(params, list) else None)
    if states and all(state is True for state in states):
        return True
    if states and all(state is False for state in states):
        return False
    return None


async def validate_request(catalog, request, preferences):
    requirements = request_requirements(request)
    modalities = set()
    for message in request["input"] if isinstance(request.get("input"), list) else ():
        content = message.get("content") if isinstance(message, dict) else None
        for part in content if isinstance(content, list) else ():
            kind = part.get("type") if isinstance(part, dict) else None
            if kind in {"input_image", "input_audio"}:
                modalities.add(kind[6:])
            if kind == "input_file":
                raise ValueError("OpenRouter file transport is unavailable")
    if not (requirements or modalities):
        return
    data = await bounded_model(catalog, request.get("model"))
    reasoning = request.get("reasoning") or {}
    profile = data.get("reasoning")
    if reasoning and isinstance(profile, dict):
        if reasoning.get("enabled") is False and profile.get("mandatory") is True:
            raise ValueError("OpenRouter model requires reasoning")
        efforts = profile.get("supported_efforts")
        if reasoning.get("effort") and isinstance(efforts, list) and reasoning["effort"] not in efforts:
            raise ValueError("Unsupported OpenRouter model reasoning effort")
    for modality in modalities:
        supported = modality_support(data, modality)
        if supported is not True:
            status = "unsupported" if supported is False else "unknown"
            raise ValueError(f"OpenRouter {modality} support is {status}")
    for parameter in requirements:
        model_parameters = data.get("supported_parameters")
        if isinstance(model_parameters, list) and parameter not in model_parameters:
            raise ValueError(f"OpenRouter model does not support {parameter}")
        if parameter_support(data, parameter, preferences.get("only", ())) is False:
            raise ValueError(f"OpenRouter endpoints do not support {parameter}")

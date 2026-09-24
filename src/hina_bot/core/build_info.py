"""Stable process/build identity fields for structured telemetry."""

from __future__ import annotations

import hashlib
import importlib.metadata
import os
import re
import uuid
from functools import lru_cache
from pathlib import Path

_GIT_SHA_RE = re.compile(r"^[0-9a-fA-F]{7,64}$")
_RUNTIME_ID = uuid.uuid4().hex[:16]


def _normalize_git_sha(value: str | None) -> str | None:
    if not value:
        return None
    value = value.strip()
    if not _GIT_SHA_RE.fullmatch(value):
        return None
    return value.lower()


def _environment_git_sha() -> str | None:
    for name in ("HINA_GIT_SHA", "GITHUB_SHA", "SOURCE_REVISION"):
        value = _normalize_git_sha(os.getenv(name))
        if value:
            return value
    return None


def _read_checkout_git_sha(git_dir: Path) -> str | None:
    try:
        head = (git_dir / "HEAD").read_text(encoding="utf-8").strip()
    except OSError:
        return None

    direct = _normalize_git_sha(head)
    if direct:
        return direct
    if not head.startswith("ref: "):
        return None

    ref = head[5:].strip()
    try:
        loose = (git_dir / ref).read_text(encoding="utf-8").strip()
    except OSError:
        loose = ""
    resolved = _normalize_git_sha(loose)
    if resolved:
        return resolved

    try:
        packed = (git_dir / "packed-refs").read_text(encoding="utf-8").splitlines()
    except OSError:
        return None
    for line in packed:
        if not line or line.startswith(("#", "^")):
            continue
        sha, _, packed_ref = line.partition(" ")
        if packed_ref == ref:
            return _normalize_git_sha(sha)
    return None


def _checkout_git_sha(start: Path) -> str | None:
    for directory in (start, *start.parents):
        marker = directory / ".git"
        if marker.is_dir():
            return _read_checkout_git_sha(marker)
        if marker.is_file():
            try:
                content = marker.read_text(encoding="utf-8").strip()
            except OSError:
                continue
            if content.startswith("gitdir:"):
                git_dir = Path(content.split(":", 1)[1].strip())
                if not git_dir.is_absolute():
                    git_dir = (directory / git_dir).resolve()
                return _read_checkout_git_sha(git_dir)
    return None


def _source_fingerprint(package_root: Path) -> str:
    digest = hashlib.sha256()
    files = sorted(
        path for path in package_root.rglob("*")
        if path.is_file()
        and "__pycache__" not in path.parts
        and path.suffix not in {".pyc", ".pyo"}
    )
    for path in files:
        relative = path.relative_to(package_root).as_posix()
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        try:
            digest.update(path.read_bytes())
        except OSError:
            continue
        digest.update(b"\0")
    return digest.hexdigest()[:20]


def _package_version() -> str:
    try:
        return importlib.metadata.version("hina-discord-bot")
    except importlib.metadata.PackageNotFoundError:
        return "unknown"


@lru_cache(maxsize=1)
def build_metadata() -> dict[str, str]:
    """Return compact metadata that identifies this code build and process."""

    package_root = Path(__file__).resolve().parents[1]
    git_sha = _environment_git_sha() or _checkout_git_sha(package_root)
    revision = f"git:{git_sha}" if git_sha else f"src:{_source_fingerprint(package_root)}"
    return {
        "app_version": _package_version(),
        "build_revision": revision,
        "runtime_id": _RUNTIME_ID,
    }


__all__ = ["build_metadata"]

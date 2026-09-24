"""Stable process/build identity fields for structured telemetry."""

from __future__ import annotations

import hashlib
import importlib.metadata
import os
import re
import subprocess
import uuid
from functools import lru_cache
from pathlib import Path

_GIT_SHA_RE = re.compile(r"^[0-9a-fA-F]{7,64}$")
_RUNTIME_ID = uuid.uuid4().hex[:16]
_PACKAGE_ROOT = Path(__file__).resolve().parents[1]


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


def _checkout_git_sha(package_root: Path) -> str | None:
    candidates = [Path.cwd(), package_root, *package_root.parents]
    checkout = next((path for path in candidates if (path / ".git").exists()), None)
    if checkout is None:
        return None
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=checkout,
            check=True,
            capture_output=True,
            text=True,
            timeout=1,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return _normalize_git_sha(result.stdout)


def _source_fingerprint(package_root: Path) -> str:
    digest = hashlib.sha256()
    files = sorted(
        path
        for path in package_root.rglob("*")
        if path.is_file()
        and "__pycache__" not in path.parts
        and path.suffix not in {".pyc", ".pyo"}
    )
    for path in files:
        digest.update(path.relative_to(package_root).as_posix().encode("utf-8"))
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

    git_sha = _environment_git_sha() or _checkout_git_sha(_PACKAGE_ROOT)
    revision = f"git:{git_sha}" if git_sha else f"src:{_source_fingerprint(_PACKAGE_ROOT)}"
    return {
        "app_version": _package_version(),
        "build_revision": revision,
        "runtime_id": _RUNTIME_ID,
    }


__all__ = ["build_metadata"]

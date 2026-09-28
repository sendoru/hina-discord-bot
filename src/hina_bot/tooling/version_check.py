"""Validate the release version and optional Git release tag."""

from __future__ import annotations

import argparse
import os
import re
import sys
import tomllib
from collections.abc import Mapping, Sequence
from pathlib import Path

_VERSION_RE = re.compile(r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$")


def read_project_version(path: Path) -> str:
    """Read the release version from pyproject.toml."""

    with path.open("rb") as handle:
        data = tomllib.load(handle)
    try:
        version = data["project"]["version"]
    except (KeyError, TypeError) as exc:
        raise ValueError(f"{path}: missing [project].version") from exc
    if not isinstance(version, str):
        raise TypeError(f"{path}: [project].version must be a string")
    return version


def expected_release_tag(version: str) -> str:
    return f"v{version}"


def validation_errors(version: str, tag: str | None = None) -> list[str]:
    """Return release-version policy violations."""

    errors: list[str] = []
    if not _VERSION_RE.fullmatch(version):
        errors.append(
            f"project version {version!r} must use plain MAJOR.MINOR.PATCH "
            "with non-negative integer components"
        )
        return errors

    if tag is not None:
        expected = expected_release_tag(version)
        if tag != expected:
            errors.append(f"release tag {tag!r} must match project version exactly: {expected!r}")
    return errors


def github_release_tag(env: Mapping[str, str]) -> str | None:
    """Return the current GitHub tag name only for tag-triggered workflows."""

    if env.get("GITHUB_REF_TYPE") != "tag":
        return None
    return env.get("GITHUB_REF_NAME") or None


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--pyproject",
        type=Path,
        default=Path("pyproject.toml"),
        help="path to pyproject.toml (default: %(default)s)",
    )
    parser.add_argument(
        "--tag",
        help="release tag to verify; defaults to GitHub's tag ref in tag-triggered workflows",
    )
    args = parser.parse_args(argv)

    try:
        version = read_project_version(args.pyproject)
    except (OSError, tomllib.TOMLDecodeError, ValueError) as exc:
        print(f"version check failed: {exc}", file=sys.stderr)
        return 1

    tag = args.tag if args.tag is not None else github_release_tag(os.environ)
    errors = validation_errors(version, tag)
    if errors:
        for error in errors:
            print(f"version check failed: {error}", file=sys.stderr)
        return 1

    suffix = f" / tag {tag}" if tag else ""
    print(f"version check OK: {version}{suffix}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

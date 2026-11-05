"""Shared dotenv template parsing, rendering, and safe file writes."""

from __future__ import annotations

import json
import os
import re
import shutil
import tempfile
from dataclasses import dataclass
from datetime import UTC, datetime
from io import StringIO
from pathlib import Path

from dotenv.parser import parse_stream

_ASSIGNMENT = re.compile(
    r"^(?P<indent>[ \t]*)(?P<comment>#[ \t]*)?"
    r"(?P<key>[A-Z][A-Z0-9_]*)=(?P<value>[^\r\n]*)(?P<newline>\r?\n|$)"
)


@dataclass(frozen=True)
class TemplateLine:
    raw: str
    indent: str = ""
    commented: bool = False
    key: str | None = None
    suffix: str = ""
    newline: str = ""


def read_text(path: str | Path) -> str:
    with Path(path).open("r", encoding="utf-8", newline="") as handle:
        return handle.read()


def _inline_comment(raw_value: str) -> str:
    """Retain template end-of-line comments, excluding hashes inside quotes."""
    quote = ""
    escaped = False
    for index, char in enumerate(raw_value):
        if escaped:
            escaped = False
        elif char == "\\" and quote:
            escaped = True
        elif char in {"'", '"'}:
            if not quote:
                quote = char
            elif quote == char:
                quote = ""
        elif char == "#" and not quote and index and raw_value[index - 1] in " \t":
            start = index
            while start and raw_value[start - 1] in " \t":
                start -= 1
            return raw_value[start:]
    return ""


def parse_template(content: str) -> tuple[list[TemplateLine], set[str]]:
    lines: list[TemplateLine] = []
    keys: set[str] = set()
    for raw in content.splitlines(keepends=True):
        match = _ASSIGNMENT.fullmatch(raw)
        if match is None:
            # Reject non-comment assignments we cannot round-trip, not arbitrary notes.
            stripped = raw.strip()
            if stripped and not stripped.startswith("#"):
                raise ValueError("Unsupported .env.example line (expected KEY=value)")
            lines.append(TemplateLine(raw))
            continue
        key = match["key"]
        if key in keys:
            raise ValueError(f"Duplicate template setting: {key}")
        keys.add(key)
        lines.append(
            TemplateLine(
                raw=raw,
                indent=match["indent"],
                commented=match["comment"] is not None,
                key=key,
                suffix=_inline_comment(match["value"]),
                newline=match["newline"],
            )
        )
    return lines, keys


def parse_values(content: str) -> dict[str, str]:
    """Parse active assignments without interpolating variable references."""
    result: dict[str, str] = {}
    for binding in parse_stream(StringIO(content)):
        if binding.error:
            raise ValueError(f"Invalid dotenv syntax on line {binding.original.line}")
        if binding.key is None:
            continue
        if binding.value is None:
            raise ValueError(f"Missing '=' for {binding.key} on line {binding.original.line}")
        if binding.key in result:
            raise ValueError(f"Duplicate dotenv setting: {binding.key}")
        result[binding.key] = binding.value
    return result


def encode_env_value(value: str) -> str:
    """Represent values in a way python-dotenv loads without losing characters."""
    if "\0" in value:
        raise ValueError("NUL is not permitted in a dotenv value")
    if not value:
        return ""
    if (
        value != value.strip()
        or any(char in value for char in "\r\n\\'\"")
        or re.search(r"(^|\s)#", value)
    ):
        return json.dumps(value, ensure_ascii=False)
    return value


def render_template(
    template: str,
    values: dict[str, str],
    *,
    disabled: frozenset[str] = frozenset(),
) -> tuple[str, set[str]]:
    lines, keys = parse_template(template)
    if not disabled.issubset(keys):
        raise ValueError(f"Unknown template keys to disable: {sorted(disabled - keys)}")
    result: list[str] = []
    for line in lines:
        if line.key is None:
            result.append(line.raw)
        elif line.key in disabled:
            # A commented assignment is absent, not the literal value "inherit".
            if line.commented:
                result.append(line.raw)
            else:
                result.append(f"{line.indent}# {line.raw[len(line.indent):]}")
        elif line.key in values:
            result.append(
                f"{line.indent}{line.key}={encode_env_value(values[line.key])}"
                f"{line.suffix}{line.newline}"
            )
        else:
            result.append(line.raw)
    return "".join(result), keys


def ensure_distinct_output(output: Path, *inputs: Path) -> None:
    target = output.expanduser().resolve()
    if any(target == source.expanduser().resolve() for source in inputs):
        raise ValueError("Output must differ from input, template, and database paths")
    if output.is_symlink():
        raise ValueError("Refusing to write through an output symlink")


def atomic_write(path: Path, content: str) -> None:
    """Write a potentially secret dotenv file atomically with mode 0600."""
    path = path.expanduser()
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def backup_env(path: Path) -> Path:
    path = path.expanduser()
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    backup = path.with_name(f"{path.name}.bak.{stamp}")
    with path.open("rb") as src, backup.open("xb") as dst:
        shutil.copyfileobj(src, dst)
    os.chmod(backup, 0o600)
    return backup

"""Load static conversational prompt text from package resources."""

from functools import cache
from importlib.resources import files


@cache
def load_prompt(name: str) -> str:
    return (
        files("hina_bot")
        .joinpath("prompts", "integration", name)
        .read_text(encoding="utf-8")
    )

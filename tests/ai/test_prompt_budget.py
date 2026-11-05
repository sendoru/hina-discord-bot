"""Track prompt sizes without making prompt wording or normal growth a CI contract.

When investigating token costs, use request-level context.size telemetry in addition
to these static-resource measurements.  Run with --junitxml to collect the properties.
"""

from importlib.resources import files

from hina_bot.ai.llm import POLICY


def _prompt(name: str) -> str:
    return files("hina_bot").joinpath("prompts", name).read_text(encoding="utf-8")


def _integration_prompt(name: str) -> str:
    return files("hina_bot").joinpath(
        "prompts", "integration", name
    ).read_text(encoding="utf-8")


def test_static_prompt_sizes_are_reported_without_hard_limits(record_property):
    resources = {
        "base": POLICY,
        "character": _prompt("hina.md"),
        "ordinary_relationship": _prompt("ordinary_relationship.md"),
        "special_dm": _prompt("special_dm.md"),
        "conversation_dm": _integration_prompt("conversation_dm.md"),
        "conversation_guild": _integration_prompt("conversation_guild.md"),
    }
    for name, prompt in resources.items():
        assert prompt.strip(), name  # an empty resource is a packaging/configuration error
        record_property(f"prompt_bytes_{name}", len(prompt.encode("utf-8")))


def test_always_on_static_prompt_size_is_reported(record_property):
    common = (
        POLICY
        + _integration_prompt("continuity.md")
        + _integration_prompt("dialogue_context.md")
        + _integration_prompt("world_core.md")
        + _prompt("hina.md")
        + _integration_prompt("turn_response.md")
        + _integration_prompt("final_output.md")
    )

    for relationship in ("ordinary_relationship.md", "special_dm.md"):
        for space in ("conversation_dm.md", "conversation_guild.md"):
            prompt = common + _prompt(relationship) + _integration_prompt(space)
            assert prompt.strip()
            record_property(
                f"always_on_chars_{relationship.removesuffix('.md')}_{space.removesuffix('.md')}",
                len(prompt),
            )

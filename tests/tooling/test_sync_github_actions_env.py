import argparse
import importlib.util
from pathlib import Path

import pytest


def _load_script():
    path = Path("scripts/sync_github_actions_env.py")
    spec = importlib.util.spec_from_file_location("sync_github_actions_env", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


sync_env = _load_script()


def _args(path, **overrides):
    values = {
        "env_file": str(path),
        "repo": "sendoru/hina-discord-bot",
        "environment": "",
        "dry_run": False,
        "include_empty": False,
        "secret": [],
        "variable": [],
    }
    values.update(overrides)
    return argparse.Namespace(**values)


@pytest.mark.parametrize("name", [
    "OPENAI_API_KEY",
    "DISCORD_TOKEN",
    "DEPLOY_SECRET",
    "DATABASE_PASSWORD",
    "SSH_PRIVATE_KEY",
])
def test_sensitive_suffixes_are_classified_as_secrets(name):
    assert sync_env.is_secret_name(name, set(), set())


@pytest.mark.parametrize("name", [
    "LLM_PROVIDER",
    "LLM_MODEL",
    "MODEL_ROUTING_MODE",
    "BOT_ADMIN_IDS",
])
def test_regular_settings_are_classified_as_variables(name):
    assert not sync_env.is_secret_name(name, set(), set())


def test_sync_uses_stdin_and_skips_empty_values(tmp_path, monkeypatch, capsys):
    env_file = tmp_path / ".env"
    env_file.write_text(
        'export OPENAI_API_KEY="secret value"\n'
        "LLM_PROVIDER=openai\n"
        "EMPTY_VALUE=\n",
        encoding="utf-8",
    )
    calls = []

    def fake_run(command, **kwargs):
        calls.append((command, kwargs))

    monkeypatch.setattr(sync_env.shutil, "which", lambda name: "/usr/bin/gh")
    monkeypatch.setattr(sync_env.subprocess, "run", fake_run)

    result = sync_env.sync(_args(env_file, environment="production"))

    assert result == (1, 1, 1)
    assert calls == [
        (["gh", "secret", "set", "OPENAI_API_KEY", "--repo", "sendoru/hina-discord-bot",
          "--env", "production"],
         {"input": "secret value", "text": True, "check": True}),
        (["gh", "variable", "set", "LLM_PROVIDER", "--repo", "sendoru/hina-discord-bot",
          "--env", "production"],
         {"input": "openai", "text": True, "check": True}),
    ]
    output = capsys.readouterr().out
    assert "secret value" not in output
    assert "SKIP     EMPTY_VALUE" in output


def test_dry_run_supports_explicit_classification_without_gh(tmp_path, monkeypatch, capsys):
    env_file = tmp_path / ".env"
    env_file.write_text(
        "CUSTOM_CREDENTIAL=credential-payload-123\n"
        "OPENAI_API_KEY=variable-payload-456\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(sync_env.shutil, "which", lambda name: None)

    result = sync_env.sync(_args(
        env_file,
        dry_run=True,
        secret=["CUSTOM_CREDENTIAL"],
        variable=["OPENAI_API_KEY"],
    ))

    assert result == (1, 1, 0)
    output = capsys.readouterr().out
    assert "DRY-RUN  SECRET CUSTOM_CREDENTIAL" in output
    assert "DRY-RUN  VARIABLE OPENAI_API_KEY" in output
    assert "credential-payload-123" not in output
    assert "variable-payload-456" not in output


def test_rejects_conflicting_or_missing_explicit_names(tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text("VALUE=1\n", encoding="utf-8")

    with pytest.raises(ValueError, match="동시에"):
        sync_env.sync(_args(env_file, dry_run=True, secret=["VALUE"], variable=["VALUE"]))
    with pytest.raises(ValueError, match="없는 이름"):
        sync_env.sync(_args(env_file, dry_run=True, secret=["MISSING"]))

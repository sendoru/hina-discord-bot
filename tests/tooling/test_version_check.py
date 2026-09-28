from pathlib import Path

from hina_bot.tooling.version_check import (
    expected_release_tag,
    github_release_tag,
    main,
    read_project_version,
    validation_errors,
)


def _write_pyproject(path: Path, version: str) -> Path:
    path.write_text(f'[project]\nname = "example"\nversion = "{version}"\n')
    return path


def test_read_project_version(tmp_path):
    path = _write_pyproject(tmp_path / "pyproject.toml", "0.1.0")
    assert read_project_version(path) == "0.1.0"
    assert expected_release_tag("0.1.0") == "v0.1.0"


def test_version_format_is_plain_semver():
    assert validation_errors("0.1.0") == []
    assert validation_errors("1.2.3") == []
    assert validation_errors("0.1") != []
    assert validation_errors("0.1.0.dev1") != []
    assert validation_errors("01.2.3") != []


def test_release_tag_must_match_project_version():
    assert validation_errors("0.2.0", "v0.2.0") == []
    assert validation_errors("0.2.0", "v0.1.9") != []
    assert validation_errors("0.2.0", "0.2.0") != []


def test_github_release_tag_only_uses_tag_refs():
    assert github_release_tag(
        {"GITHUB_REF_TYPE": "tag", "GITHUB_REF_NAME": "v0.2.0"}
    ) == "v0.2.0"
    assert github_release_tag(
        {"GITHUB_REF_TYPE": "branch", "GITHUB_REF_NAME": "main"}
    ) is None


def test_main_uses_github_tag_environment(monkeypatch, tmp_path, capsys):
    path = _write_pyproject(tmp_path / "pyproject.toml", "0.3.0")
    monkeypatch.setenv("GITHUB_REF_TYPE", "tag")
    monkeypatch.setenv("GITHUB_REF_NAME", "v0.2.0")

    assert main(["--pyproject", str(path)]) == 1
    assert "must match project version exactly" in capsys.readouterr().err

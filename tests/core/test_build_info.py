from hina_bot.core import build_info


def test_build_metadata_prefers_explicit_git_sha(monkeypatch):
    sha = "a" * 40
    monkeypatch.setenv("HINA_GIT_SHA", sha)
    build_info.build_metadata.cache_clear()
    try:
        metadata = build_info.build_metadata()
        assert metadata["build_revision"] == f"git:{sha}"
        assert metadata["app_version"]
        assert len(metadata["runtime_id"]) == 16
    finally:
        build_info.build_metadata.cache_clear()


def test_build_metadata_falls_back_to_source_fingerprint(monkeypatch):
    monkeypatch.setattr(build_info, "_environment_git_sha", lambda: None)
    monkeypatch.setattr(build_info, "_checkout_git_sha", lambda _path: None)
    build_info.build_metadata.cache_clear()
    try:
        metadata = build_info.build_metadata()
        assert metadata["build_revision"].startswith("src:")
        assert len(metadata["build_revision"]) == len("src:") + 20
    finally:
        build_info.build_metadata.cache_clear()

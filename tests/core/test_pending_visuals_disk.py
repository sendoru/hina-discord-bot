"""Disk store for photos awaiting memory extraction (#472)."""

import logging
import os
import time
from pathlib import Path

import pytest

from hina_bot.core.pending_visuals import (
    DiskPendingVisualStore,
    MemoryVisual,
    discard_all_pending_visuals,
    pending_visual_root,
)

REALM, CHANNEL, USER = "guild:1", "10", "100"


def png(size: int = 100, tag: bytes = b"a") -> bytes:
    return (b"\x89PNG\r\n\x1a\n" + tag * size)[:size]


def jpeg(size: int = 100) -> bytes:
    return (b"\xff\xd8\xff\xe0" + b"j" * size)[:size]


def visual(message_id="1", index=1, data=None, mime="image/png") -> MemoryVisual:
    return MemoryVisual(str(message_id), index, mime, data if data is not None else png())


def photo(root: Path, *, realm="guild-1", channel="10", user="100", message="1", number=1,
          ext="png") -> Path:
    return root / realm / channel / user / f"{message}.{number}.{ext}"


def age(path: Path, seconds: float) -> None:
    moment = time.time() - seconds
    os.utime(path, (moment, moment))


def tree(root: Path) -> set[str]:
    if not root.exists():
        return set()
    return {str(p.relative_to(root)) for p in root.rglob("*")}


@pytest.fixture
def root(tmp_path):
    return tmp_path / "visual_pending"


@pytest.fixture
def store(root):
    return DiskPendingVisualStore(root)


def put(store, message="1", *, realm=REALM, channel=CHANNEL, user=USER, visuals=None):
    return store.put(realm, channel, user, message,
                     visuals if visuals is not None else [visual(message)])


def test_the_root_sits_next_to_the_database(tmp_path):
    assert pending_visual_root(tmp_path / "data" / "hina.sqlite3") == (
        tmp_path / "data" / "visual_pending"
    )


def test_put_then_get_round_trips_in_order(store):
    first, second = png(120, b"x"), jpeg(90)
    assert put(store, "7", visuals=[
        visual("7", 1, first), visual("7", 2, second, "image/jpeg"),
    ]) == 2

    found = store.get(REALM, CHANNEL, USER, ["7"])

    assert list(found) == ["7"]
    assert [(v.message_id, v.index, v.mime_type, v.data) for v in found["7"]] == [
        ("7", 1, "image/png", first), ("7", 2, "image/jpeg", second),
    ]


def test_get_returns_only_requested_turns_that_have_photos(store):
    put(store, "1")
    put(store, "2")

    assert list(store.get(REALM, CHANNEL, USER, ["2", "3"])) == ["2"]
    assert store.get(REALM, CHANNEL, USER, []) == {}
    assert store.get(REALM, "999", USER, ["1"]) == {}
    assert store.get("guild:2", CHANNEL, USER, ["1"]) == {}
    assert store.get(REALM, CHANNEL, "200", ["1"]) == {}


def test_a_missing_root_is_simply_empty(store, root):
    assert not root.exists()
    assert store.get(REALM, CHANNEL, USER, ["1"]) == {}
    assert store.discard_all() == 0
    assert store.discard_user(REALM, USER) == 0
    assert store.sweep(set(), max_age_seconds=1) == 0
    assert not root.exists()


@pytest.mark.skipif(os.name != "posix", reason="POSIX permissions")
def test_folders_and_files_are_owner_only(store, root):
    put(store)

    for path in (root, root / "guild-1", root / "guild-1" / "10",
                 root / "guild-1" / "10" / "100"):
        assert path.stat().st_mode & 0o777 == 0o700, path
    assert photo(root).stat().st_mode & 0o777 == 0o600


def test_only_real_images_within_the_limits_are_kept(store, root):
    store.max_turn_images = 2
    store.max_file_bytes = 200
    accepted = put(store, "1", visuals=[
        visual(data=b"%PDF-1.7 not an image", mime="image/png"),  # signature mismatch
        visual(data=png(), mime="application/pdf"),                 # unsupported type
        visual(data=b""),                                           # empty
        visual(data=png(300)),                                      # over the file limit
        visual(index=5, data=png(50)),
        visual(index=6, data=png(60, b"b")),
        visual(index=7, data=png(70, b"c")),                        # beyond the per-turn cap
    ])

    assert accepted == 2
    assert sorted(p.name for p in photo(root).parent.iterdir()) == ["1.1.png", "1.2.png"]
    # Numbering follows the stored order, not the caller's labels.
    assert [v.index for v in store.get(REALM, CHANNEL, USER, ["1"])["1"]] == [1, 2]


def test_nothing_is_stored_when_no_image_qualifies(store, root):
    assert put(store, visuals=[visual(data=b"junk")]) == 0
    assert put(store, visuals=[]) == 0
    assert not root.exists()


def test_a_repeated_put_replaces_the_turns_photos(store):
    put(store, "1", visuals=[visual("1", 1, png(80, b"o")), visual("1", 2, png(80, b"p"))])
    put(store, "1", visuals=[visual("1", 1, png(90, b"n"))])

    found = store.get(REALM, CHANNEL, USER, ["1"])["1"]

    assert [v.data for v in found] == [png(90, b"n")]


@pytest.mark.parametrize("realm, channel, user, message", [
    ("guild:../x", "10", "100", "1"),
    ("guild:1", "../escape", "100", "1"),
    ("guild:1", "10", "1/2", "1"),
    ("guild:1", "10", "100", "../1"),
    ("other:1", "10", "100", "1"),
    ("guild:1", "", "100", "1"),
    ("guild:1", "10", "100", "x.png"),
])
def test_ids_that_are_not_digits_never_reach_the_filesystem(
    store, root, tmp_path, realm, channel, user, message
):
    before = tree(tmp_path)

    assert store.put(realm, channel, user, message, [visual(message)]) == 0
    assert store.get(realm, channel, user, [message]) == {}
    assert store.discard_messages(realm, channel, user, [message]) == 0
    assert store.discard_user(realm, user) == 0
    assert store.discard_channel(realm, channel) == 0
    assert store.discard_realm(realm) == 0

    assert tree(tmp_path) == before and not root.exists()


def test_a_linked_root_is_refused(tmp_path):
    target = tmp_path / "elsewhere"
    target.mkdir()
    link = tmp_path / "visual_pending"
    link.symlink_to(target, target_is_directory=True)

    with pytest.raises(OSError):
        DiskPendingVisualStore(link).put(REALM, CHANNEL, USER, "1", [visual()])

    assert list(target.iterdir()) == []


def test_get_does_not_follow_a_linked_photo(store, root, tmp_path):
    put(store)
    secret = tmp_path / "secret.png"
    secret.write_bytes(png(50, b"s"))
    path = photo(root)
    path.unlink()
    path.symlink_to(secret)

    assert store.get(REALM, CHANNEL, USER, ["1"]) == {}
    assert secret.exists()


def test_discarding_never_follows_a_linked_folder(store, root, tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "keep.png").write_bytes(png())
    put(store)
    user_dir = root / "guild-1" / "10" / "100"
    for child in user_dir.iterdir():
        child.unlink()
    user_dir.rmdir()
    user_dir.symlink_to(outside, target_is_directory=True)

    store.discard_user(REALM, USER)

    assert not user_dir.exists() and not user_dir.is_symlink()
    assert (outside / "keep.png").exists()


def test_a_failed_write_leaves_no_partial_turn(store, root, monkeypatch):
    real_replace = os.replace
    calls = []

    def flaky(source, destination):
        calls.append(destination)
        if len(calls) == 2:
            raise OSError("disk full")
        return real_replace(source, destination)

    monkeypatch.setattr(os, "replace", flaky)

    with pytest.raises(OSError):
        put(store, "1", visuals=[visual("1", 1, png(60, b"x")), visual("1", 2, png(60, b"y"))])

    leftovers = [p for p in root.rglob("*") if p.is_file()]
    assert leftovers == []
    assert store.get(REALM, CHANNEL, USER, ["1"]) == {}


# -- least recently used eviction ---------------------------------------------------------


def test_the_least_recently_used_photo_is_evicted_first(root):
    store = DiskPendingVisualStore(root, max_bytes=250)
    put(store, "1")
    put(store, "2")
    age(photo(root, message="1"), 300)
    age(photo(root, message="2"), 200)

    put(store, "3")  # 300 bytes would exceed 250: the oldest goes

    assert set(store.get(REALM, CHANNEL, USER, ["1", "2", "3"])) == {"2", "3"}
    assert store.evicted == 1


def test_reading_a_photo_protects_it_from_eviction(root):
    store = DiskPendingVisualStore(root, max_bytes=250)
    put(store, "1")
    put(store, "2")
    age(photo(root, message="1"), 300)
    age(photo(root, message="2"), 200)
    store.get(REALM, CHANNEL, USER, ["1"])  # a retried extraction read it just now

    put(store, "3")

    assert set(store.get(REALM, CHANNEL, USER, ["1", "2", "3"])) == {"1", "3"}


def test_eviction_makes_room_across_users_and_channels(root):
    store = DiskPendingVisualStore(root, max_bytes=250)
    put(store, "1", channel="10", user="100")
    put(store, "2", channel="20", user="200")
    age(photo(root, channel="10", user="100", message="1"), 500)

    put(store, "3", channel="30", user="300")

    assert store.get(REALM, "10", "100", ["1"]) == {}
    assert store.get(REALM, "20", "200", ["2"]) != {}
    assert store.get(REALM, "30", "300", ["3"]) != {}


def test_one_user_cannot_push_out_another_users_photos(root):
    store = DiskPendingVisualStore(root, max_user_images=2, max_turn_images=2)
    put(store, "9", channel="10", user="200")
    for number in (1, 2, 3):
        put(store, str(number), channel="10", user="100")
        age(photo(root, message=str(number)), 1000 - number)

    mine = store.get(REALM, "10", "100", ["1", "2", "3"])
    assert set(mine) == {"2", "3"}  # their own oldest went
    assert set(store.get(REALM, "10", "200", ["9"])) == {"9"}


def test_the_user_limit_counts_every_channel_of_a_realm(root):
    store = DiskPendingVisualStore(root, max_user_images=2)
    put(store, "1", channel="10", user="100")
    put(store, "2", channel="20", user="100")
    age(photo(root, channel="10", message="1"), 500)

    put(store, "3", channel="30", user="100")

    assert store.get(REALM, "10", "100", ["1"]) == {}
    assert store.get(REALM, "20", "100", ["2"]) != {}


def test_the_user_byte_limit_applies(root):
    store = DiskPendingVisualStore(root, max_user_bytes=250)
    put(store, "1")
    put(store, "2")
    age(photo(root, message="1"), 300)

    put(store, "3")

    assert set(store.get(REALM, CHANNEL, USER, ["1", "2", "3"])) == {"2", "3"}


def test_a_turn_larger_than_the_whole_store_is_refused_without_evicting(root):
    store = DiskPendingVisualStore(root, max_bytes=150)
    put(store, "1")

    assert put(store, "2", visuals=[visual("2", 1, png(100)), visual("2", 2, png(100, b"b"))]) == 0

    assert store.evicted == 0 and set(store.get(REALM, CHANNEL, USER, ["1", "2"])) == {"1"}


def test_the_total_limit_can_be_changed_at_runtime(store):
    store.configure(max_bytes=123)
    assert store.max_bytes == 123
    store.configure()
    assert store.max_bytes == 123


# -- deletion -----------------------------------------------------------------------------


def _populate(store):
    put(store, "1", realm="guild:1", channel="10", user="100")
    put(store, "2", realm="guild:1", channel="10", user="200")
    put(store, "3", realm="guild:1", channel="20", user="100")
    put(store, "4", realm="guild:2", channel="10", user="100")
    put(store, "5", realm="dm:100", channel="30", user="100")


def _left(store):
    held = set()
    for realm, channel, user, message in (
        ("guild:1", "10", "100", "1"), ("guild:1", "10", "200", "2"),
        ("guild:1", "20", "100", "3"), ("guild:2", "10", "100", "4"),
        ("dm:100", "30", "100", "5"),
    ):
        if store.get(realm, channel, user, [message]):
            held.add(message)
    return held


def test_discard_messages_removes_only_those_turns(store, root):
    _populate(store)

    assert store.discard_messages("guild:1", "10", "100", ["1", "404"]) == 1

    assert _left(store) == {"2", "3", "4", "5"}


def test_discard_user_spans_the_realms_channels_only(store):
    _populate(store)

    assert store.discard_user("guild:1", "100") == 2

    assert _left(store) == {"2", "4", "5"}


def test_discard_channel_removes_every_user_in_it(store):
    _populate(store)

    assert store.discard_channel("guild:1", "10") == 2

    assert _left(store) == {"3", "4", "5"}


def test_discard_realm_removes_the_whole_realm(store):
    _populate(store)

    assert store.discard_realm("guild:1") == 3

    assert _left(store) == {"4", "5"}


def test_discard_all_empties_the_store_but_keeps_the_root(store, root):
    _populate(store)

    assert store.discard_all() == 5

    assert root.is_dir() and list(root.iterdir()) == []


def test_empty_folders_are_pruned(store, root):
    put(store)

    store.discard_messages(REALM, CHANNEL, USER, ["1"])

    assert root.is_dir() and list(root.iterdir()) == []


# -- sweep --------------------------------------------------------------------------------


def test_sweep_keeps_photos_of_pending_turns_and_removes_the_rest(store):
    _populate(store)
    pending = {("guild:1", "10", "100", "1"), ("dm:100", "30", "100", "5")}

    removed = store.sweep(pending, max_age_seconds=3600)

    assert removed == 3 and _left(store) == {"1", "5"}


def test_sweep_expires_old_photos_even_for_pending_turns(store, root):
    put(store, "1")
    put(store, "2")
    age(photo(root, message="1"), 7200)
    pending = {(REALM, CHANNEL, USER, "1"), (REALM, CHANNEL, USER, "2")}

    assert store.sweep(pending, max_age_seconds=3600) == 1

    assert set(store.get(REALM, CHANNEL, USER, ["1", "2"])) == {"2"}


def test_sweep_uses_the_given_clock(store, root):
    put(store, "1")
    modified = photo(root, message="1").stat().st_mtime
    pending = {(REALM, CHANNEL, USER, "1")}

    assert store.sweep(pending, max_age_seconds=100, now=modified + 50) == 0
    assert store.sweep(pending, max_age_seconds=100, now=modified + 500) == 1


def test_sweep_clears_abandoned_temp_files_but_not_fresh_ones(store, root):
    put(store, "1")
    folder = photo(root).parent
    stale = folder / ".9.1.png.tmp"
    fresh = folder / ".8.1.png.tmp"
    stale.write_bytes(b"half")
    fresh.write_bytes(b"half")
    age(stale, 3600)

    store.sweep({(REALM, CHANNEL, USER, "1")}, max_age_seconds=10**6)

    assert not stale.exists() and fresh.exists()


def test_sweep_leaves_files_it_did_not_write(store, root):
    put(store, "1")
    stranger = photo(root).parent / "notes.txt"
    stranger.write_text("operator note")
    outside = root / "README"
    outside.write_text("hello")

    store.sweep(set(), max_age_seconds=1)

    assert stranger.read_text() == "operator note" and outside.read_text() == "hello"
    assert store.get(REALM, CHANNEL, USER, ["1"]) == {}


def test_stats_counts_files_and_bytes(store):
    put(store, "1", visuals=[visual("1", 1, png(100)), visual("1", 2, png(60, b"b"))])

    assert store.stats() == (2, 160)


# -- offline helper -----------------------------------------------------------------------


def test_discard_all_pending_visuals_clears_the_databases_photos(tmp_path):
    database = tmp_path / "hina.sqlite3"
    DiskPendingVisualStore(pending_visual_root(database)).put(
        REALM, CHANNEL, USER, "1", [visual()]
    )

    assert discard_all_pending_visuals(database) == 1
    assert discard_all_pending_visuals(database) == 0
    assert discard_all_pending_visuals(tmp_path / "missing" / "hina.sqlite3") == 0


def test_discard_all_pending_visuals_logs_a_file_error_without_raising(
    tmp_path, monkeypatch, caplog
):
    def broken(self):
        raise PermissionError("denied: secret-path")

    monkeypatch.setattr(DiskPendingVisualStore, "discard_all", broken)

    with caplog.at_level(logging.WARNING, logger="hina"):
        assert discard_all_pending_visuals(tmp_path / "hina.sqlite3") == 0

    assert "Pending visual cleanup failed (PermissionError)" in caplog.text
    assert "secret-path" not in caplog.text

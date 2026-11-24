"""Native audio awaiting a later memory extraction uses visual-store-safe lifecycle (#548)."""

import os
import time

from hina_bot.core.pending_audio import (
    DiskPendingAudioStore,
    MemoryAudio,
    NullPendingAudioStore,
    pending_audio_root,
)
from hina_bot.core.routing import Scope
from hina_bot.core.store import Store
from hina_bot.tooling.reset_conversation import reset_conversation

OGG = b"OggS" + b"\\x00" * 40
MP3 = b"ID3" + b"\\x00" * 40


def clip(mid="1", data=OGG, mime="audio/ogg"):
    return MemoryAudio(str(mid), 1, mime, data)


def put(store, mid="1", *, user="100", channel="10", realm="guild:1", inputs=None):
    return store.put(realm, channel, user, str(mid), [clip(mid)] if inputs is None else inputs)


def test_audio_round_trip_and_repeated_put(tmp_path):
    disk = DiskPendingAudioStore(tmp_path / "audio_pending")
    assert put(disk) == 1
    assert put(disk, "2", inputs=[clip("2", MP3, "audio/mpeg")]) == 1
    assert [(x.mime_type, x.data) for x in disk.get("guild:1", "10", "100", ["1", "2"])["2"]] == [("audio/mpeg", MP3)]
    assert put(disk, "1", inputs=[clip("1", MP3, "audio/mpeg")]) == 1
    assert [x.data for x in disk.get("guild:1", "10", "100", ["1"])["1"]] == [MP3]
    assert disk.stats()[0] == 2


def test_only_signature_valid_supported_audio_is_stored(tmp_path):
    disk = DiskPendingAudioStore(tmp_path / "audio_pending")
    assert put(disk, inputs=[
        clip(data=b"not audio", mime="audio/ogg"),
        clip(data=OGG, mime="audio/wav"),
        clip(data=OGG, mime="image/png"),
    ]) == 0
    assert not (tmp_path / "audio_pending").exists()


def test_audio_is_owner_scoped_and_has_bounded_eviction(tmp_path):
    disk = DiskPendingAudioStore(tmp_path / "audio_pending", max_bytes=len(OGG) * 2 + 3)
    assert put(disk, "1") == put(disk, "2") == put(disk, "3") == 1
    assert set(disk.get("guild:1", "10", "100", ["1", "2", "3"])) == {"2", "3"}
    assert disk.get("guild:1", "10", "200", ["3"]) == {}
    assert disk.get("guild:2", "10", "100", ["3"]) == {}


def test_audio_sweep_ttl_and_unknown_turn(tmp_path):
    disk = DiskPendingAudioStore(tmp_path / "audio_pending")
    put(disk, "1")
    put(disk, "2")
    path = tmp_path / "audio_pending" / "guild-1" / "10" / "100" / "1.1.ogg"
    os.utime(path, (time.time()-3600, time.time()-3600))
    assert disk.sweep({("guild:1", "10", "100", "1")}, max_age_seconds=600) == 2
    assert disk.stats() == (0, 0)


def test_discard_user_channel_realm_and_all(tmp_path):
    disk = DiskPendingAudioStore(tmp_path / "audio_pending")
    for idx, (realm, channel, user) in enumerate([
        ("guild:1", "10", "100"), ("guild:1", "20", "100"),
        ("guild:1", "10", "200"), ("guild:2", "10", "100"),
    ], 1):
        put(disk, str(idx), realm=realm, channel=channel, user=user)
    assert disk.discard_user("guild:1", "100") == 2
    assert disk.discard_channel("guild:1", "10") == 1
    assert disk.discard_realm("guild:2") == 1
    assert disk.discard_all() == 0


def test_get_refuses_linked_audio_and_directory(tmp_path):
    disk = DiskPendingAudioStore(tmp_path / "audio_pending")
    assert put(disk) == 1
    path = disk.root / "guild-1" / "10" / "100" / "1.1.ogg"
    outside = tmp_path / "outside.ogg"
    outside.write_bytes(OGG)
    path.unlink()
    path.symlink_to(outside)
    assert disk.get("guild:1", "10", "100", ["1"]) == {}
    assert disk.discard_channel("guild:1", "10") == 0
    assert outside.read_bytes() == OGG


def test_store_purge_and_reset_clear_pending_audio(tmp_path):
    db = tmp_path / "hina.sqlite3"
    store = Store.create(str(db))
    scope = Scope(1, 10, 100, True)
    try:
        assert isinstance(store.pending_audio, DiskPendingAudioStore)
        store.add(scope, 1, "히나야", "응")
        assert put(store.pending_audio) == 1
        store.forget(scope)
        assert store.pending_audio.stats() == (0, 0)
        store.add(scope, 2, "히나야", "응")
        assert put(store.pending_audio, "2") == 1
    finally:
        store.close()
    reset_conversation(db, backup_dir=tmp_path/"backups", offline_confirmed=True,
                       discard_pending=True)
    assert DiskPendingAudioStore(pending_audio_root(db)).stats() == (0, 0)


def test_in_memory_store_is_noop():
    store = Store.create(":memory:")
    try:
        assert isinstance(store.pending_audio, NullPendingAudioStore)
        assert put(store.pending_audio) == 0
    finally:
        store.close()

"""Audio byte signature detection shared by Discord input and pending-memory storage."""

def sniff_audio_mime(data: bytes) -> str | None:
    if data.startswith(b"OggS"):
        return "audio/ogg"
    if data[:4] == b"RIFF" and data[8:12] == b"WAVE":
        return "audio/wav"
    if data.startswith(b"fLaC"):
        return "audio/flac"
    if data[:4] == b"\\x1a\\x45\\xdf\\xa3":
        return "audio/webm"
    if data[4:8] == b"ftyp" and data[8:12] not in {b"heic", b"avif"}:
        return "audio/mp4"
    if data.startswith(b"ID3") or (
        len(data) >= 2 and data[0] == 0xFF and data[1] & 0xE0 == 0xE0
    ):
        return "audio/mpeg"
    return None

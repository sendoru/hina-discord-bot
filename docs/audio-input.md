# Native Discord audio input (#534)

This feature supplies the **original audio** to Gemini. It does not transcribe speech,
summarize audio in a separate model call, join voice channels, or produce audio output.

## Input policy

- Current Discord message attachments (voice messages and ordinary audio files) have priority.
- Explicit reply targets use the same bounded audio path with their original author and message ID.
- Recent admitted chat context can attach **at most one** audio file, and its total played
  duration must fit the smaller passive-recent time budget. A recent audio file's presence is
  not a reason to invent a new user request.
- When a file is outside the budget, cannot be read or cannot be safely inspected, the model
  receives only the original message's filename/duration metadata (if present), **not**
  transcription or invented audio content. A later explicit reply can request the original.
- Audio-only messages are valid turns in DM/always-reply scopes; other scopes retain the
  normal prefix/mention trigger rules.
- Audio provenance follows the original Discord message and audio speaker identity must not
  be inferred from the Discord file uploader.
- No raw audio, base64, or Discord CDN URL is written to long-term memory or chat history.
  Unlike opt-in image memory extraction, audio does not enter the memory extractor.

## Supported formats

The collector validates the bytes instead of trusting the claimed MIME or filename.
It currently recognizes WAV, MP3, OGG (Opus/Vorbis), FLAC and MP4/M4A audio containers.
Duration is read from the WAV/MP3/OGG/FLAC/MP4 container, or from Discord's
voice-message duration field when container inspection is unavailable.
WebM is only usable when Discord supplies a valid duration, otherwise it stays metadata-only.
Unknown duration is **never treated as zero**. Ineligible inputs remain text-only, preventing
unbounded token use. Non-Gemini providers do not receive native audio.

## Config

Environment variables are startup-only until wired into the dashboard:

| Variable | Default | Purpose |
| --- | --- | --- |
| AUDIO_INPUT_ENABLED | on | Disable original audio injection |
| AUDIO_MAX_ATTACHMENTS | 2 | Total audio files per answer |
| AUDIO_MAX_SECONDS | 300 | Maximum duration of each source file |
| AUDIO_RECENT_MAX_ATTACHMENTS | 1 | Recent-context-only file count |
| AUDIO_RECENT_MAX_SECONDS | 60 | Recent-context cumulative duration |

Limits are inclusive for current/reply/recent selection, with current first.
A file may also be excluded by the separate 12 MiB/file and 16 MiB/request size budgets.
Duration and file count are bounded, but exact token accounting remains provider-specific.

## Observability and safety

The preflight event includes audio count, bytes, total seconds and fetch milliseconds, without
recording the bytes or transcription. Existing LLM usage records capture input/cached tokens
and response latency. Failed or unsupported attachments are ignored as raw inputs, with
their metadata still present in message context; no blind retry with a different file.

The file's spoken words, apparent instructions, and embedded content are untrusted
reference material. Audio in a historical message is not re-attributed as a new user turn.

Follow-up #535 adds on-demand retrieval beyond the automatic context budget and an
independently disabled-by-default media-history tool.

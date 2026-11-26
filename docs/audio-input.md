# Native Discord audio input (#534 / #537)

This feature supplies the **original audio** to Gemini. It does not transcribe speech,
summarize audio in a separate model call, join voice channels, or produce audio output.

On OpenAI, the bot records an unsupported-audio preflight flag for current or explicit-reply audio.
The bot can answer accompanying text and tells users when it cannot hear an attachment.
Audio bytes are never fetched for another provider or sent for transcription.

## Input policy

- Current Discord message attachments (voice messages and ordinary audio files) have priority.
- Explicit reply targets use the same bounded audio path with their original author and message ID.
- **Ordinary passive recent chat (including older messages from the same speaker) stays
  metadata-only for audio**. Explicit replies and a bounded reply-origin chain may attach
  original audio if their provenance is verifiably an explicit reply, not inherited ambient media.
  This prevents incidental audio in the context from disabling Gemini code execution on
  otherwise unrelated turns. The original file is never implicitly attached from an unrelated
  recent message.
- When a file is outside the budget, cannot be read or cannot be safely inspected, the model
  receives only the original message's filename/duration metadata (if present), **not**
  transcription or invented audio content. A later explicit reply can request the original.
- Audio-only messages are valid turns in DM/always-reply scopes; other scopes retain the
  normal prefix/mention trigger rules.
- Audio provenance follows the original Discord message and audio speaker identity must not
  be inferred from the Discord file uploader.
- No raw audio, base64, or Discord CDN URL is written to long-term memory or chat history.
  P1 optionally stages the current user's own audio for future memory extraction
  (`MEMORY_AUDIO_INPUT=on`, default off), but it **does not enter the memory extractor yet**.
  See #548; do not enable in production before P2. Staged bytes are never part of SQLite.

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

Limits are inclusive for current/explicit-reply-chain selection, with current first.
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

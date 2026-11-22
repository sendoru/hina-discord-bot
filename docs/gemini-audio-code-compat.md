# Gemini native audio + code execution compatibility investigation (#537)

## Observed failure

On 2026-11-22, the production Gemini request including native Discord audio and
the declared `code_execution` tool failed with HTTP 400 INVALID_ARGUMENT:

```text
The mime type: audio/ogg is not supported for code execution.
```

A subsequent native-audio test without code execution did not reproduce that
failure. PR #536 temporarily omitted code execution **only when a Gemini answer
request contains original audio**. This keeps the existing Search/URL Context/
local function tools available, but prevents code execution in those turns.

The failure was triggered by a request with both media and a tool declaration.
It does **not** establish that the model attempted to invoke the code tool, and
it does **not** establish that every audio codec or endpoint combination fails.

## Official documentation and related reports

- Gemini [audio understanding](https://aistudio.google.com/docs/audio) supports
  direct audio input.
- Gemini [code execution](https://aistudio.google.com/docs/code-execution) is
  separately supported. Its document lists text/CSV-oriented file input and an
  image+code-execution example, but does not explicitly guarantee audio+code
  execution in a single request.
- Google Cloud's [code execution limits](https://docs.cloud.google.com/gemini-enterprise-agent-platform/models/tools/code-execution)
  list permitted inline file types such as source/text/CSV/images, not audio.
  **This is corroborating documentation for a related API, not proof of
  identical restrictions in the Gemini Developer API.**
- Other developers have reported an analogous MIME rejection for
  [PDF+code_execution](https://discuss.ai.google.dev/t/pdf-generated-by-code-execution-cannot-be-passed-back-as-input/177965).
  This suggests a media/tool compatibility boundary rather than an OGG decoder
  bug, but does not replace a controlled Gemini Developer API reproduction.

## Reproduction

The opt-in diagnostic script directly calls the project's Gemini provider adapter
without passing through the answer assembler's audio gating. This means it can
reproduce the unsupported combination without disabling production safeguards.

```bash
uv run python -m hina_bot.tooling.gemini_audio_code_probe --dry-run

# An actual short OGG voice note is preferred to the generated WAV baseline:
uv run python -m hina_bot.tooling.gemini_audio_code_probe \
    --audio /path/to/short-voice-message.ogg \
    --model gemini-3.5-flash-lite

# Repeat with a separately obtained, short MP3/M4A when desired.
uv run python -m hina_bot.tooling.gemini_audio_code_probe \
    --audio /path/to/short-audio.m4a \
    --model gemini-3.5-flash-lite

# If only one API is in question:
uv run python -m hina_bot.tooling.gemini_audio_code_probe \
    --api generate_content --audio /path/to/short-voice-message.ogg
```

The command loads `GEMINI_API_KEY` from the existing environment or
`.env.local`/`.env`. Do not post the key or raw audio to GitHub.
Without `--audio`, the script generates a one-second silent WAV for a baseline
test. Provided files are capped at 4 MiB and 10 seconds by default (an explicit
`--max-seconds` can increase this to at most 60). It performs six low-token API
calls by default and **may incur API charges**:

| API | Input | Tool declaration | Purpose |
| --- | --- | --- | --- |
| Interactions | text | code_execution | Positive control for code tool |
| Interactions | audio | none | Positive control for audio |
| Interactions | audio | code_execution | Suspected incompatible combination |
| generateContent | text | code_execution | Code tool control for second API |
| generateContent | audio | none | Audio control for second API |
| generateContent | audio | code_execution | Same combination on second API |

Optional `--include-function` adds audio+custom-function tests, relevant to
#535. Optional `--include-search` adds audio+Google Search tests; these may have
additional search charges. Requests use `store=false`, do not write media or
response content to files, and emit only status codes, limited provider error
diagnostics, and elapsed milliseconds. Responses may say `requires_action`
when the API accepted a client-side function call.

### Interpretation

- `text+code=200`, `audio-only=200`, `audio+code=400` isolates a
  code/media combination restriction for that **API + model + MIME type**.
- `audio-only=400` means the audio transport/model/MIME must be fixed before
  drawing conclusions about code execution.
- `text+code=400` means the baseline model/tool setup isn't supported.
- A different outcome between Interactions and generateContent means the
  workaround might be API-specific rather than a model-wide restriction.
- An HTTP 200 demonstrates request acceptance, **not** that the model actually
  called or executed Python. The test deliberately does not claim that.

The real API matrix has **not** yet been run from this repository branch:
no production Gemini credentials are available to the investigation environment.

## Next decision

Keep the current fail-safe code tool gating until live matrix results are
available. If audio+code is unsupported across the relevant APIs and formats,
avoid blind retries, re-encoding, or always-on STT. Consider a deliberate
second *text-only* computation stage for genuinely computation-heavy audio
questions, or a narrowly scoped local calculation function, with strict
provenance and per-turn token/latency budgets. Such a solution should preserve
raw audio in the initial understanding step and should be developed separately
after the compatibility boundary is established.

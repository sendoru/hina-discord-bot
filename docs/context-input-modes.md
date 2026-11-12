# Answer context input modes (#506)

The `CONTEXT_INPUT_MODE` runtime setting controls only provider-facing conversation
serialization. History collection, #482 TTL, #504 session filtering, reply provenance,
privacy egress, memory selection and reset boundaries are unchanged.

| Mode | Provider input | Status |
| --- | --- | --- |
| `legacy` (default) | All authorized contextual fields in a separate reference `user_input`, followed by current user text | Production default |
| `structured` (B) | Background references first; `current_speaker`, `current_interaction`, `active_reply_chain` next to the current text in the last user step | Opt-in |
| `structured_with_replay` (B + C1) | B plus contiguous complete stored **DM** exchanges as synthetic `user_input`/`model_output` steps **on Gemini only** | Experimental |

## Scope and limitations

- C1 uses the same bounded DM `conversation_history` already admitted by the
  privacy and session rules. It does **not** fetch more history, create new storage,
  or add an extra LLM request. Server multi-speaker context is never replayed.
- Replayed exchanges are removed from the reference JSON to avoid duplicating text.
  Incomplete/unpaired rows, older rows preceding such a break, and
  `reference_derived` history-lookup answers remain reference data. Only a
  contiguous replayable suffix is used.
- This is a **synthetic text-only transcript**, not original Gemini Interaction
  state. The stored DB does not retain earlier thought signatures, tool call
  steps, image bytes or other hidden state. Provider acceptance and reply quality
  must be verified using the actual configured Gemini API/model before enabling C1.
  A passing unit test for the payload shape cannot establish API compatibility.
- The existing untrusted-reference boundary remains: historical references are
  reference data, and the user's current message is still the final `user_input`.
  The C1 replay itself is explicitly experimental because it can strengthen the
  apparent continuity of an earlier incorrect assistant response.
- To roll back, set `CONTEXT_INPUT_MODE=legacy` in Runtime Config. Non-Gemini
  providers use B without synthetic replay when the C1 mode is selected.

## Verification

Use identical authorized input and instructions to compare `legacy`,
`structured`, and `structured_with_replay` with the same Gemini model:
1. User correction after a mistaken Hina response.
2. Natural short follow-up after an ordinary DM exchange.
3. Unrelated-topic switch, indirect quotes and reference-derived old answers.
4. Role attribution in server multi-speaker exchanges.
5. Input/cached tokens, total request latency and API errors.

A/B/C1 model quality comparison requires intentional paid test calls; it is **not**
automatically triggered by this change. Do not enable C1 as the default before it
has passed live compatibility and response-quality evaluation.

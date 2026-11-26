# Retrieval decisions for Lore and personal memory

Select `decision` independently for lore and personal memory. Both default to `full`.
Only active Decision selection makes billable Decisions requests.

## Selection and fallback

- The selector sends the domain's relevance rules and all eligible candidates once in the
  shared input. One short `noul` question per candidate references its ID and the shared rules.
  Lore and memory requests run concurrently when both are enabled. Gemini uses `GEMINI_API_KEY` and `gemini-decision-1`. OpenAI uses its [native Decisions endpoint](https://developers.openai.com/api/reference/resources/decisions/methods/create) with `gpt-6-luna` and one predicate per candidate; no Responses generation or store parameter.
  The selector follows the main provider. Unsupported providers use the existing full fallback locally.
  Enabling decision mode authorizes these extra same-provider requests.
- Lore uses every reviewed static entry allowed by the current community and awareness settings.
  Selection keeps the source wording and awareness, timeline and uncertainty labels. An inference
  with explicit evidence dependencies is packed together with those sources when they fit.
  `LORE_MAX_ITEMS` and `LORE_MAX_CHARS` bound the selected static block. Core persona and world
  instructions are always included.
- Memory uses the same eligible, deduplicated records as manual full mode. In the owner’s DM these may include sensitive personal memories from any origin. Guild requests exclude sensitive records even when explicitly mentioned. Implicit relationship text never enters the Decisions request.
- The initial score threshold is 0.65. Empty selection is a valid result. A 1,000-question cap,
  conservative 240,000-byte body cap and configurable `DECISION_TIMEOUT_SECONDS` deadline
  (default 1.5 seconds, range 0.25–30) bound each domain. OpenAI partitions candidates into at most three sequential batches of up to 400, sharing the deadline and aggregate byte cap. Any batch failure discards all scores; observed usage is summed. Change it in Dashboard Runtime Config
  or with `/config set DECISION_TIMEOUT_SECONDS <seconds>` without restarting the bot. An
  oversized pool is skipped in full. Missing or invalid answers, timeouts and HTTP errors
  fall back without a retry. Active lore falls back to `full`. Active memory uses the same eligible full projection as manual `full` for that
  request only. A successful empty selection still uses the Decision path. A changed memory snapshot is rechecked before injection.
- The turn sent to Decisions is bounded text: the visible message, egress-approved reply context
  and short same-speaker continuity. Memory uses only the owner's user messages and the bot's
  permitted causal replies. Memory text is sent only to the memory endpoint request.

## Data and monitoring

Use a billing-enabled Gemini Developer API project for production turns. The
[Decisions API request](https://aistudio.google.com/docs/decisions-api#request) has no Interactions
storage field. The app does not create an Interaction, upload files, enable explicit caching or
export request bodies to AI Studio. Google describes limited abuse-monitoring retention under the
paid terms. Review the project's data-sharing and retention settings before enabling either mode
on real turns. [Gemini Developer API data retention](https://aistudio.google.com/docs/zdr)

The usage log records counts, request bytes, scores by band, timing, usage and fallback reasons.
Trace detail also shows selected IDs from the stored context snapshot. The existing `answer` and
Discord exchange rows provide input, cached and output token counts for the same turn. Compare
those with `decision.lore` and `decision.memory`, including the character counts for the candidate
selection and the default projection.

## Configuration migration

Former lore `v1` and either domain's `decision_shadow` load as `full`. Startup migrates stored overrides and snapshots. Switching from lexical lore to full can increase input size. Historical traces keep their recorded mode.

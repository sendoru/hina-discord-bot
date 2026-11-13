# Gemini Decisions for Lore and personal memory

| Runtime setting | Default | Observation | Active |
|---|---|---|---|
| `RAG_MODE` | `full` | `decision_shadow` | `decision` |
| `PERSONAL_MEMORY_READ_MODE` | `legacy` | `decision_shadow` | `decision` |

Set each independently in Dashboard **Runtime Config** or with `/config set`. An observation
mode uses its default answer context and evaluates the same candidate pool as active mode. Both
modes make a billable Decisions request when candidates exist. Runtime knowledge stays on the
existing bounded lookup. Lore tool, web search and URL Context keep their existing mode-specific
behavior.

## Selection and fallback

- The selector sends the domain's relevance rules and all eligible candidates once in the
  shared input. One short `noul` question per candidate references its ID and the shared rules.
  Lore and memory requests run concurrently when both are enabled. The client uses
  `GEMINI_API_KEY` and `gemini-decision-1` on the Gemini Developer API. The answer provider is
  configured independently.
- Lore uses every reviewed static entry allowed by the current community and awareness settings.
  Selection keeps the source wording and awareness, timeline and uncertainty labels. An inference
  with explicit evidence dependencies is packed together with those sources when they fit.
  `LORE_MAX_ITEMS` and `LORE_MAX_CHARS` bound the selected static block. Core persona and world
  instructions are always included.
- Memory applies owner, memory mode, disclosure, activity and time eligibility before egress.
  Sensitive notes require a keyword in the current message. The deterministic tiers handle
  directives, quiet boundaries, session follow-up, the background-only core profile and this
  turn's explicit T4 approvals. Decisions selects relevant notes for T2. It cannot grant a
  proactive-use hint or modify disclosure or annotation judgments. Raw relationships stay out of
  the selection; the answer receives the numeric relationship profile.
- The initial score threshold is 0.65. Empty selection is a valid result. A 1,000-question cap,
  conservative 240,000-byte body cap and 1.5-second request deadline bound each call. An
  oversized pool is skipped in full. Missing or invalid answers, timeouts and HTTP errors
  fall back without a retry. Active lore falls back to `full`. Active memory falls back to a
  scope-filtered full projection of eligible notes with the deterministic tiers and use hints
  applied. A changed memory snapshot is rechecked before injection.
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
selection and the default projection. Memory shadow coverage is recorded separately from
`tiered_shadow` coverage. The coverage check is based on keyword or content-word matches in the
reply.

## Transition from `shadow`

On bot startup, the existing SQLite override and startup snapshot for
`personal_memory_read_mode` are normalized from `"shadow"` to `"tiered_shadow"` if present.
The update is idempotent and preserves the override and its timestamp. The dashboard, reports and
environment export can read old records before startup. Existing `PERSONAL_MEMORY_READ_MODE=shadow`
in an environment file still loads as `tiered_shadow`. Use the canonical spelling for new writes
and update deployment environment files during the transition. On rollback to a release that
predates this rename, restore `"shadow"` for this DB override and `shadow` in the environment file
if applicable. The independent `ROUTING_CLASSIFIER_MODE=shadow` still observes model/web routing.

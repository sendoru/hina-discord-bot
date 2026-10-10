# Retrieval v2 rollout, shadow comparison, and rollback (#316)

Part of #310. This is the production migration boundary for Retrieval v2.

## Modes

`RETRIEVAL_V2_MODE` is runtime-editable and has three values:

| mode | answer context | v2 work |
| --- | --- | --- |
| `off` | legacy only | none |
| `shadow` | legacy only | background comparison |
| `active` | v2 on success, legacy on v2 failure/timeout | awaited |

The code default is **off**. Changing the runtime setting to `off` is the immediate
rollback path; it does not require a process restart.

Shadow and off do not change the model-facing legacy context. Active mode attaches the
typed v2 `KnowledgeBundle` and serializes its sections through the existing provider
boundary.

## Semantic calibration gate

The runtime does not ship guessed Gemini cosine thresholds.

```text
RETRIEVAL_V2_SEMANTIC_REJECT=0
RETRIEVAL_V2_SEMANTIC_STRONG=0
```

means semantic factual and ambient embedding admission is not calibrated. Exact profile,
lexical factual, and exact relation lanes can still be evaluated, but semantic production
rollout is disabled.

Before setting non-zero thresholds:

1. run the #313 live Gemini calibration;
2. inspect positive / hard-negative / unrelated distributions and token/latency output;
3. run the end-to-end rollout eval with those measured thresholds;
4. only then write the measured reject/strong pair into runtime settings.

The runtime validates `reject < strong`. The backend cache key already includes provider,
model, revision, dimensions, and formatting version, so a different embedding configuration
must be recalibrated rather than silently reusing old thresholds.

## Shadow latency isolation

Shadow retrieval is started after the legacy information plan is ready but is not awaited
by the answer path.

Safety rules:

- at most one Retrieval v2 shadow job is in flight per LLM instance;
- a new shadow observation is skipped with `shadow_busy` instead of queueing;
- `RETRIEVAL_V2_TIMEOUT_SECONDS` independently limits v2 work;
- shadow timeout/error cannot fail or alter the answer;
- shutdown waits for already-started shadow work so logs/cache writes are not abandoned.

This is intentionally stricter than maximizing shadow sample count. Interactive answer
latency has priority.

## Active fallback

Active mode awaits v2 within its own timeout. On timeout or exception:

```text
v2 failure
   ↓
content-free telemetry
   ↓
original legacy InformationPlan
   ↓
normal answer
```

There is no second answer/model request and no partial v2 context.

On success:

- profile → deterministic profile path;
- relation → exact pair grounding;
- factual → lexical-first + calibrated semantic-only recall;
- ambient → calibrated semantic insight lane for conversational scenes;
- reaction → reviewed reaction lane if such rows exist;
- BundleComposer owns item/character budgets and deduplication;
- #315 evaluates the composed factual/relation evidence for `LOCAL_THEN_WEB`;
- request assembly emits facts/relations as `lore_reference`, plus typed
  `character_insights` and `optional_reactions`.

Ambient and reaction rows never suppress factual web fallback.

## Invocation policy

A semantic factual query is considered only when:

- the request is factual/relation/event; or
- a conversational turn has resolved canonical entity context; or
- a conversational turn has an authorized causal anchor.

Plain conversation with neither entity nor anchor does not make a factual semantic call.
Profile requests never use factual semantic embeddings.

Ambient semantic retrieval is a separate conversation-only lane. It uses the bounded
#314 scene representation and still requires measured calibration.

## Telemetry

`retrieval_v2.shadow` and `retrieval_v2.active` events contain no query/reference
content.

Recorded comparison fields include:

- mode/status/calibration state;
- factual semantic invocation and per-lane semantic status;
- legacy/v2 selected counts;
- truncated SHA-256 hashes of selected candidate ids;
- overlap count/rate;
- candidate and selected counts per lane;
- zero-result and relation-hit indicators;
- lexical vs semantic-only factual selections;
- semantic cache hits/misses;
- provider-reported embedding prompt tokens and request count;
- v2 elapsed time;
- composer duplicate count and per-section serialized chars;
- #315 evidence sufficient/reason/predicate/state;
- timeout/error/fallback reason.

Raw user text, candidate content, semantic query text, vectors, provider error bodies and raw
candidate ids are not written by this telemetry path.

Aggregate existing usage logs with:

```bash
python -m hina_bot.tooling.retrieval_v2_shadow_report data/logs/usage.jsonl
```

or write a report:

```bash
python -m hina_bot.tooling.retrieval_v2_shadow_report \
  data/logs/usage.jsonl \
  --output data/logs/retrieval-v2-shadow-report.json
```

## Evaluation

### 1. Factual semantic calibration

Use the existing #313 calibration command documented in
`docs/retrieval-v2-hybrid.md`. Do not treat its trial threshold as production approval
without reviewing held-out results.

### 2. End-to-end rollout fixture

Validate the packaged fixture without a key:

```bash
python -m hina_bot.tooling.retrieval_v2_rollout_eval --validate-only
```

The fixture covers:

- profile lookup;
- relationship/school-year grounding;
- direct first meeting;
- semantic paraphrase for prior awareness;
- explicit unknown evidence;
- ambient insight;
- reaction-lane zero-result behavior while no reviewed reaction corpus exists;
- unrelated programming query.

With a Gemini key and measured thresholds:

```bash
GEMINI_API_KEY=... \
python -m hina_bot.tooling.retrieval_v2_rollout_eval \
  --reject <measured-reject> \
  --strong <measured-strong> \
  --ambient-min 0.75 \
  --output data/logs/retrieval-v2-rollout-eval.json
```

The tool does not modify runtime settings.

## Suggested migration sequence

1. **off** — merge code, no behavior change.
2. **shadow, calibration disabled** — compare deterministic/profile/relation/lexical v2
   composition and validate telemetry/latency isolation.
3. Run live Gemini calibration and end-to-end eval.
4. **shadow, calibrated** — observe real semantic selections, zero-result rate, cache/token
   usage, latency, #315 sufficiency, and legacy/v2 overlap.
5. Review representative held-out answers and false-positive cases.
6. **active, calibrated** — limited operator rollout with legacy fallback still present.
7. After a stable observation window, remove the legacy retrieval path in a separate
   cleanup rather than inside the rollout switch PR.

No automatic percentage rollout is added here; the bot's current deployment is small enough
that an explicit runtime mode is easier to reason about and immediately reversible.

## Rollback

At any point:

```text
RETRIEVAL_V2_MODE = off
```

through the existing runtime settings editor/command returns the next turn to the legacy
retrieval path immediately. If semantic quality alone is suspect, setting both semantic
thresholds back to zero disables semantic/ambient embedding admission while leaving exact
v2 code available for diagnosis.

## Remaining operational gate

This repository/CI environment has no live Gemini key. Therefore this implementation can
prove:

- configuration/rollback behavior;
- full deterministic integration;
- non-blocking shadow orchestration;
- active legacy fallback;
- content-free telemetry;
- fixture validity and reporting tools.

It **cannot** claim that a particular live cosine threshold is production-ready. That final
threshold and rollout decision must come from the documented live calibration/eval in the
deployment environment.

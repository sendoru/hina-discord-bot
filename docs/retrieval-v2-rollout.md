# Retrieval v2 rollout, shadow telemetry and rollback

Issue: #316. Part of #310.

Retrieval v2 stays behind an explicit runtime rollout mode. The legacy retrieval path is
kept as the rollback path in this milestone.

## Modes

`RETRIEVAL_V2_MODE` is hot-reloadable:

- `off`: only the legacy answer-context path runs.
- `shadow`: the legacy plan is authoritative. Retrieval v2 runs in a detached task and
  only emits comparison telemetry.
- `active`: a successfully completed v2 bundle replaces the legacy lore context for that
  turn. Timeout, failure or calibration gating keeps the already-built legacy plan.

Immediate rollback:

```text
/config set RETRIEVAL_V2_MODE off
```

Shadow tasks have bounded concurrency. Backpressure skips the extra shadow run instead of
delaying the answer. The whole v2 run also has `RETRIEVAL_V2_TIMEOUT_SECONDS`.

## Active calibration gate

Semantic production rollout is fail-closed. `active` requires all of:

- `GEMINI_API_KEY`;
- factual reject/strong thresholds from live calibration;
- ambient reject/strong thresholds from live calibration;
- `RETRIEVAL_V2_CALIBRATION_BACKEND_KEY` equal to the current embedding backend identity.

For the initial configuration that identity is derived from:

- model: `gemini-embedding-2`
- revision: `1`
- output dimensionality: `RETRIEVAL_V2_EMBEDDING_DIMENSIONS`
- document task representation version

A stale calibration from a different model/revision/dimension cannot activate semantic
production retrieval. Shadow mode may still run deterministic profile/relation/lexical
parts when live semantic calibration is absent.

The repository deliberately does not ship live threshold values. Validate fixtures
without credentials first:

```bash
python -m hina_bot.tooling.retrieval_calibration --validate-only
python -m hina_bot.tooling.ambient_calibration --validate-only
```

Then, in an environment with `GEMINI_API_KEY`, produce separate live factual and ambient
reports:

```bash
python -m hina_bot.tooling.retrieval_calibration \
  --output data/logs/retrieval-v2-factual-calibration.json

python -m hina_bot.tooling.ambient_calibration \
  --output data/logs/retrieval-v2-ambient-calibration.json
```

Review the held-out distributions/zero-result behavior, copy the matching `backend_key`
and approved factual/ambient threshold pairs into the rollout settings, then enable shadow
first. Neither CLI writes production settings.

## End-to-end path

The rollout engine executes:

```text
RoutingPlan
  → resolved RetrievalRequest / canonical entities / optional relation_pair
  → exact profile or relationship lanes
  → factual lexical-first + calibrated semantic recall
  → scene-aware ambient retrieval for conversation turns
  → optional reaction lane
  → BundleComposer
  → #315 structured evidence sufficiency
  → section-aware model context
```

Static and runtime candidates share the factual candidate pool without source priority.
`COMMUNITY_LORE=false` excludes community reaction material.

Ambient scene text remains bounded to already-authorized context: the current visible turn
is carried by the request, while the scene may add the causal anchor, at most two authorized
same-speaker turns and a compact aggregate relationship signal. Raw memory and full channel
history are not embedded.

## Active prompt sections

When v2 is active, `InformationPlan` carries the composed `KnowledgeBundle` to request
assembly.

- relation + factual rows → `lore_reference`
- ambient rows → `character_insights`
- reaction rows → `reaction_guides`

Section-specific policies are attached only when that section exists. Ambient and reaction
rows therefore cannot silently become factual evidence; relation rows keep direction/time
grounding semantics.

For `LOCAL_THEN_WEB`, the final v2 bundle is passed through #315. Sufficient structured
evidence suppresses web lookup; insufficient evidence requires web lookup when web search
is enabled. Other deterministic live/web rules remain authoritative.

## Shadow telemetry

The `retrieval.v2` event contains only bounded/content-free metadata. It can include:

- rollout mode, completion/fallback status and selected context;
- semantic calibration gate reason;
- legacy/v2 selected counts;
- short SHA-256 hashes of stable candidate ids;
- legacy/v2 overlap and zero-result flags;
- relation/factual/ambient/reaction selection and candidate counts;
- factual invocation/skip reason;
- lexical/semantic admitted and semantic-rejected counts;
- cache hit/miss counts;
- embedding prompt-token count and embedding request count;
- candidate/query embedding latency;
- factual/ambient/total retrieval latency;
- composer duplicate count and per-section serialized character use;
- #315 sufficiency boolean/reason/predicate/answer state.

It must not contain:

- query/visible text;
- candidate content/summary/reference prose;
- raw scene text;
- raw memory/history;
- embedding vectors.

## Recommended rollout sequence

1. keep `off` while deploying the code;
2. run the factual and ambient live calibration CLIs;
3. configure the exact backend key and measured thresholds;
4. switch to `shadow`;
5. review representative cases and telemetry, especially zero-result behavior, semantic-only
   recall, relation correctness, ambient false positives and web-fallback sufficiency;
6. switch selected deployment/configuration to `active`;
7. rollback immediately to `off` on quality, latency, provider or cost regressions.

Do not remove the legacy retrieval implementation in this milestone.

## Regression/eval fixtures

The end-to-end deterministic fixture is:

```text
evals/retrieval_v2_rollout.jsonl
```

It covers exact factual/profile, exact relation, explicit entity mismatch, ambient scene,
community reaction and unrelated ordinary-chat zero-result behavior. The test suite executes
that fixture through the complete request-builder → retrieval lanes → BundleComposer →
#315 sufficiency chain with a deterministic fake embedding backend.

Semantic distribution approval remains split into the dedicated live-calibration fixtures:

- `evals/retrieval_v2_calibration.jsonl` for factual semantic recall/hard negatives;
- `evals/retrieval_v2_ambient.jsonl` for ambient insight scenes/hard negatives.

CI validates the integration contract without external credentials; live Gemini reports are
the operator gate before active rollout.

## Representative review set

Before active rollout, include examples for:

- explicit Hina profile/factual QA;
- Hina/Hoshino school year, relation and addressing;
- prior-awareness vs first-meeting time/direction distinctions;
- semantic paraphrases that lexical matching misses;
- unrelated and entity-mismatch zero-result cases;
- explicit unknown guards;
- causal-anchor follow-ups and topic changes;
- overwork/rest, ordinary leisure, praise/support and private-time ambient scenes;
- unrelated everyday chat that should produce no ambient insight;
- community reaction queries and unrelated non-meme queries.

The unit/integration suite covers the deterministic contracts. Live Gemini distribution,
latency and cost approval remain an operator step because CI intentionally has no API key.

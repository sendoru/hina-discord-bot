# Structured memory

Structured long-term memory is the persistent personal-memory system used by normal replies.
Legacy personal `summaries` are removed on Store startup after their cursor baseline is migrated.
`shared_summaries` remains a separate public-context path for direct guild interactions.

## Why this exists

Structured items separate remembered content from disclosure policy, provenance, lifecycle state,
and relationship evidence. This avoids maintaining a second scoped-string personal-memory source and
lets normal replies, reconciliation, inspection, and future editing operate on the same persistent data.

A public Discord guild channel contributes to a guild-level disclosure space. A private guild channel
is narrower: memories formed there are automatically full only in that same channel. This preserves
the existing `public_at_capture` privacy boundary while still keeping `origin_channel_id` for
provenance and channel-level deletion.

## Item fields

Each item stores:

- `user_id`: owner of the memory. Cross-user access is always denied.
- `content`: normalized memory text.
- `kind`: `fact`, `event`, `preference`, `relationship`, `boundary`, or `task`.
- `origin_realm`: `dm:<user>` or `guild:<guild>` where the memory was formed.
- `origin_channel_id`: channel provenance inside that realm.
- `origin_public_at_capture`: whether the guild channel was public when the source was captured.
- `disclosure`: `local`, `implicit`, or `global`.
- `source_message_ids`: source Discord message ids for provenance/auditing.
- `confidence`: extractor confidence in the range 0..1.
- `relationship_evidence`: sparse positive 1..4 evidence vector used only by `relationship` items.
  Supported axes are `familiarity`, `comfort`, `casualness`, `teasing_tolerance`,
  `support_openness`, and `task_orientation`. Missing/zero means no stored positive evidence,
  never dislike or rejection.

## Annotations (memory v2, #449/#451)

Store schema v2 adds `memory_item_annotations`, one row per memory item. It holds regenerable
judgments about the item rather than new facts: when the memory applies (`time_scope`, `stability`,
`event_at`, `recurrence`), how Hina may use it (`usage_mode`, `directive_type`, `audience`,
`importance`), retrieval hints (`slot`, `keywords`), the quoted source (`evidence`, `observed_at`),
and lifecycle markers (`last_confirmed_at`, `confirmations`, `last_used_at`, `proactive_*`).
`memory_items` remains the source of truth and is not altered.

- An annotation applies to one `item_revision`. When an admin edit raises the item revision, the
  annotation is stale until it is re-annotated (`hina-backfill-annotations` or a dashboard edit).
  Stale or missing annotations fall back to the conservative defaults in #449 §4.1, except that a
  stale directive stays a directive until it is re-annotated (principle 6).
- `annotator` records who wrote it (`extractor`, `consolidator`, `backfill`, `admin`); admin edits win.
- The database enforces the cross-field rules: `directive_type` exists exactly for `directive`
  items, `stability` only for `ongoing` items, and `sensitive` items always have `audience=owner`.
- Relationship items are not annotated; they keep the relationship projection path.
- Every path that deletes a memory item (`/forget`, channel/realm/all purge, dashboard permanent
  deletion) deletes its annotation in the same transaction. `hina-reset-conversation` and
  `hina-reset-analysis-data` preserve annotations together with the memory items. A quote that
  another item inherited from the deleted one is removed from that item too (see
  [Reinforcing an annotation from a duplicate](#reinforcing-an-annotation-from-a-duplicate)).

The table is created by `hina-prepare-db` (see [DB initialization](db-initialization.md)). In this
stage the response path does not read it yet; extraction v2 and backfill populate it, and the response
path stays legacy until P2.

`observed_at` and `last_confirmed_at` use the stored turn time (UTC, `YYYY-MM-DD HH:MM:SS`), so
they compare as strings. `event_at` is a local calendar date (`YYYY-MM-DD` or `YYYY-MM`) in
`RUNTIME_TIMEZONE`.

### Extraction v2

The shadow extractor now returns the annotation fields next to each non-relationship item. Each
input turn carries `at_local` and `weekday` in `RUNTIME_TIMEZONE`, so a relative date such as
"다음 주 화요일" becomes an absolute `event_at`. The prompt walks through tense, stability, usage,
audience and importance in that order; `kind` remains only for compatibility.

The extractor, the directive verifier and the backfill accept their rows either in the requested
envelope (`{"items": [...]}`, `{"results": [...]}`) or as a bare array, which some models return
instead. Only the envelope is relaxed: every row is validated as before, and any other shape is
still an invalid response.

The parser never trusts a judgment it cannot check (#449 principle 6):

- `evidence` must be a substring of a cited user utterance (NFKC, case and whitespace ignored).
  Otherwise it is cleared, `evidence_mismatch` is recorded and `ambient` drops to `contextual`.
- A past or completed marker in the evidence of an `ongoing` item records `past_marker_ongoing` and
  drops `ambient` to `contextual`; the time scope itself is left for review.
- Missing judgments fall back to `contextual`/`unknown`; a `boundary` item falls back to a boundary
  directive so a boundary is never lost. Malformed `event_at`/`recurrence` values are dropped.
  `recurrence` is `weekly:TUE 19:00`, `weekly:TUE` when no clock time was given, several weekdays
  such as `weekly:SAT,SUN`, or `monthly:15`. Empty strings count as omitted.
- `planned`/`episodic` items without a date record `undated_event`; P2 expires them 30 days after
  `observed_at`. `recurring` items without a usable `recurrence` record `recurrence_missing`, since
  P2 cannot compute their next occurrence.
- `audience=server` is narrowed to `owner` for sensitive items and for anything not captured in a
  public guild channel.
- An invalid legacy label no longer drops an otherwise valid item (#449 decision 15): an unknown
  `disclosure` (for example `sensitive` copied from `usage_mode`) becomes the narrowest `local`
  (`disclosure_defaulted`), and an unknown `kind` becomes `fact` (`kind_defaulted`). An unknown kind
  on a row with relationship evidence is still rejected, since it may be a relationship.

- With `MEMORY_VISUAL_INPUT=on` the extractor may also receive the user's own photos held for the
  batch ([vision input](vision-input.md#장기-기억-추출용-임시-보관-memory_visual_input), #472). A
  photo only enriches what the user said: an item that relies on one names the turn in `visual_refs`,
  must still quote the user's utterance as `evidence` (otherwise it is rejected), is capped at
  confidence 0.75 and carries the `visual_grounded` lint flag. `visual_refs` is honoured only for
  turns that had a photo in that request; a text-only retry gets no visual credit.

Directive items get one more small request (`verify_memory_directives`) before the commit. The
verifier sees only the directive items and the user utterances they cite: `keep` marks the item
verified, `temporary` (boundaries only) keeps it verified as a short ongoing boundary, and `reject`
demotes it to `contextual` with `directive_rejected`. A failed or malformed verification keeps the
directive but unverified (`directive_unverified`), so a boundary is not silently dropped. Directives
are not demoted by the evidence or tense checks above; the verifier decides.

Annotations commit in the same transaction as their items and the extraction cursor. A same-space
re-mention of an active item is counted as a confirmation (`confirmations + 1`, latest
`last_confirmed_at`) instead of a new row, and so is an applied `duplicate` reconciliation. A
`corrects` proposal whose new item has the same `slot` as its target is auto-applied from confidence
0.8 instead of 0.95. Automatic reconciliation no longer requires matching `kind` labels: a fact and a
preference describing the same memory may be merged, and only a relationship/non-relationship pair
is refused as `kind_mismatch` (#449 decision 15). Relationship observations are skipped when an active relationship item from the
same channel and visibility, created within 14 days of the batch, already shows every axis at least
as high (#449 decision 18).

### Reinforcing an annotation from a duplicate

A backfilled memory has no quote to check (`source_unavailable`), so it stays `contextual` however
often the user mentions it again. When an applied `duplicate` reconciliation merges a new item into
such a target (#471, #449 principle 2), the target takes over the duplicate's judgment and quote in
the same transaction as the extraction batch. Reinforcement happens only when all of these hold:

- The target is in the extraction scope's own disclosure space: the same realm, and a public origin
  or the same channel (the selector's rule). A DM duplicate of a server memory, or any target from
  another realm, is only reconfirmed.
- The target's annotation has no quote and is not an `admin` judgment. A target that already has a
  quote keeps its judgment and is only reconfirmed, as is a duplicate below the 0.95 threshold.
- The duplicate's annotation kept a verified quote (the parser's evidence, tense and audience checks).
- No directive is demoted. A new directive reinforces only when the batch verifier kept it
  (`verified`), a directive target is never replaced by a non-directive, and a `boundary` is never
  replaced by a `style` directive, since style is not carried across spaces (§7.2).

The replaced fields are `time_scope`, `stability`, `event_at`, `recurrence`, `usage_mode`,
`directive_type`, `audience`, `importance`, `slot`, `keywords`, `explicit`, `evidence`, `verified`
and the lint flags. The annotation becomes `annotator=extractor` at the target's current
revision, and `audience` is narrowed again against the target's own origin. `observed_at`,
`confirmations`, `last_confirmed_at`, `last_used_at` and the proactive markers stay with the
target; the usual reconfirmation still applies. Re-extracting the exact same sentence creates no
new row, so it is only reconfirmed.

The duplicate remains a superseded row (`superseded_by` = the target) with its own annotation, which
is where the quote can be traced from; the dashboard lists these rows as **Evidence items**. Because
the quote is user text, deleting the evidence items takes it from the target as well (§4.6):
dashboard permanent deletion and a channel purge clear the target's quote in the same transaction
unless another superseded duplicate still carries the same quote. (`/forget` and realm or full purges
delete both sides.) An automated judgment then falls back to what it can justify without a quote:
`ambient` becomes `contextual`, `source_unavailable` is recorded, and a directive stays a directive
but unverified. An `admin` judgment is kept and only the quote is removed. The link is the equality
of the quotes, so a target whose own original quote happens to equal a deleted duplicate's quote
loses it too; the deletion contract takes priority over keeping it.

### Annotation backfill

`hina-backfill-annotations` annotates active non-relationship memories that have no annotation
and refreshes automated annotations made stale by a later item edit. Run it with the bot stopped;
`--dry-run` prints only counts. With `--yes --offline-confirmed` it backs up the database, then sends
each owner's items in batches (`--batch-size`, default 10; `--limit` caps the total) to the memory
model with the shared field rules.

- The model sees the stored content, its kind, the stored time (local time and weekday), and the
  owner's own source utterances when conversation retention still has them. The same evidence,
  tense and audience checks as extraction apply, so items without surviving sources cannot become
  `ambient` and are flagged `source_unavailable`.
- A `boundary`-kind item stays a boundary directive candidate even if the model chooses otherwise;
  only the verifier can demote it.
- Directives with sources get the verification request; without sources they stay unverified
  directives.
- Rows are written as `annotator=backfill` for the item's current revision. A stale row keeps its
  original observation, confirmation, use and proactive markers.
- Admin annotations are never replaced; stale ones are counted as `stale_admin_review` for the
  dashboard queue. Owners whose memory writes are disabled are skipped and never sent to the model.
- A failed batch writes nothing and is reported by reason: an exception type, `empty_response`,
  `invalid_json` or `no_items` (no row carried an integer `index`). Output never contains memory
  text.

The `memory.shadow_extraction` completed event adds `annotated_items`, `reconfirmed_items`,
`reinforced_annotations` (targets that took a duplicate's judgment, count only),
`suppressed_relationship_items`, `directive_items`, `verified_directives` and `rejected_directives`.

## Personal memory read modes

For owner memory in guild replies, `PERSONAL_MEMORY_CROSS_SCOPE` defaults to `on`. Reads outside the current disclosure space require either this runtime setting or the owner's explicit opt-in through `/memory cross-scope`. Turning off the default retains only explicit opt-ins. Enabling the default never creates one. This setting does not affect owner DMs or P4 member profiles.

The first filter checks active ownership and disclosure. In the owner's DM, records from every origin are eligible, including sensitive records. In a guild, a sensitive annotation excludes the record even when the current message repeats its keywords. The filter uses no conversation history. Eligible duplicate records (normalized text or an existing cluster) contribute once, preferring the current channel. Original records are retained.

`full` sends all eligible factual content. `decision` scores that pool and sends the selected records. An empty selection stays empty. On a request error the same full projection is used for that turn. Implicit relationship evidence can contribute to a numeric profile without exposing its text. Server lookup through `recall_memory` uses its independent P4 permissions.

## Disclosure semantics

The policy primitive returns one of three access levels:

- `full`: the memory content may be supplied to the response layer.
- `implicit`: only a derived relationship/interaction signal may cross the space; the original content
  must not be supplied verbatim.
- `hidden`: the item must not enter the response context.

A memory is in the same disclosure space when it comes from the current DM, the same private guild
channel, or a public channel in the current guild. A private guild memory does not become guild-wide
merely because the target channel belongs to the same server.

The owner's DM is a special private aggregate space: once ownership matches, structured memories from
all of that user's guilds/channels are available there regardless of disclosure. This never permits
another user's memory to enter the DM.

| disclosure | same disclosure space | DM -> guild | guild A -> guild B | owner DM |
| --- | --- | --- | --- | --- |
| `local` | full | hidden | hidden | full |
| `implicit` | full | implicit | implicit | full |
| `global` | full | full | full | full |

`PUBLIC_SERVER_MEMORY_IN_DM` remains relevant to the legacy summary path while migration is in
progress, but it no longer controls structured-memory access: the owner's DM aggregates all of that
owner's structured memory.

## Phase 1: storage and privacy foundation

Phase 1 adds the schema, typed policy model, storage API, deletion integration, and privacy matrix
tests. It does not change any response context.

## Phase 2: shadow extraction

Structured extraction has its own persisted cursor and cadence. By default it processes four turns at
a time (`STRUCTURED_MEMORY_EVERY=4`).

- extracted rows are written to `memory_items` and are the persistent personal-memory source,
- the extractor receives its own fixed-size pending batch, bounded causal `context`, and DM Hina replies
  used to interpret short follow-ups; public-server extraction still omits Hina replies,
- when an existing database still has the removed personal `summaries` table, Store startup copies each
  missing cursor baseline from `summaries.through_id` with `INSERT OR IGNORE` and then drops that table,
  so already-initialized structured cursors remain authoritative and retained history is not replayed,
- every extracted item must cite one or more actual `message_id` values from that batch,
- `origin_public_at_capture` is derived conservatively from the cited source rows rather than the
  channel's visibility at extraction time; a private/non-exportable source is never upgraded to public,
- unknown source ids, invalid enums/confidence, overlong content, malformed JSON, and provenance-less
  rows are rejected instead of repaired,
- exact retry duplicates are suppressed,
- identical content/kind/disclosure/evidence in the same origin channel with the same
  public-at-capture visibility is suppressed even when it occurs in a later batch;
  different relationship evidence is retained as a separate observation,
- memory candidates now reserve space for relationship and non-relationship items
  before ranking the bounded 24-item extraction context by simple textual overlap;
  overlap never authorizes memory access or decides whether an item is a duplicate,
- a valid `{"items":[]}` result advances the structured cursor, while API failures, empty responses,
  malformed top-level JSON, or storage failures leave the batch pending for retry,
- structured extraction, personal summary, and shared summary run as separate memory tasks so failures
  do not block each other,
- the normal message path still waits for the fixed four-turn batch, but a background stale-tail sweep
  checks hourly by default and may flush 2-3 pending turns once the oldest pending turn is at least eight
  hours old; one-turn tails remain deferred to avoid turning every isolated message into an extraction,
- the stale sweep shares the normal channel/user locks and model-concurrency semaphore, skips scopes whose
  current memory mode does not allow writes, and leaves the cursor unchanged on failure so the next sweep
  can retry,
- API usage is visible as the `extract_memory_items_shadow` operation, with content-free
  `memory.shadow_extraction` lifecycle events alongside the existing summary telemetry.

The extractor classifies `kind`, `disclosure`, and `confidence`; these classifications are deliberately
not trusted by the response layer yet. Real stored rows can therefore be reviewed before deciding
thresholds, reconciliation rules, or read-path behavior.

## Phase 2.6: reconciliation lifecycle

New extraction batches are compared with a bounded set of recent **active** items. In a server,
candidates stay inside the current user's disclosure space. In the owner's DM, candidates may come from
any realm/channel owned by that user. The extractor may propose exactly one relationship for a new item:

- `duplicate`: materially the same fact/preference was extracted again,
- `corrects`: the current turns explicitly correct, replace, or fix an earlier item,
- `conflicts`: both claims cannot comfortably be true, but the current batch does not clearly establish
  which one should replace the other.

Every proposal is still retained in `memory_reconciliation_proposals` for inspection. The first
automatic lifecycle policy is intentionally conservative:

- matching `kind` is required; relationship duplicates are eligible only when
  both items share the exact origin realm, channel, capture visibility, disclosure
  and numeric evidence, while relationship corrections and different evidence
  observations remain review-only,
- relation confidence must be at least `0.95`,
- high-confidence `duplicate` supersedes the newly extracted duplicate and keeps the existing target
  active; exact same-scope canonical-content duplicates can be skipped before insert,
- high-confidence explicit `corrects` supersedes the old target and keeps the new item active,
- `conflicts`, relationship-memory proposals, lower-confidence proposals, and kind mismatches remain
  observation-only.

`memory_items.status` is either `active` or `superseded`. A superseded item keeps its full content,
source provenance, timestamps, and proposal history, and `superseded_by` records the active replacement
or canonical duplicate target. Normal Store retrieval and reconciliation candidate selection return
active items only; audit/dashboard paths can still inspect superseded rows.

Candidate ids are accepted only when they were actually supplied to the model. The Store independently
re-checks ownership and origin rules when applying a lifecycle transition: DM reconciliation may target
any item owned by the current user, while server reconciliation targets only the current disclosure
space. A proposal can therefore never widen memory visibility merely by superseding another item.

Exact retry suppression deliberately scans active and superseded history, but not retracted rows. This
allows a later genuine re-observation to create memory again after an operator retracts an item. If item storage or proposal
application partially succeeds but the extraction cursor cannot advance, the retry reuses the existing
item/proposal instead of creating another row. Lifecycle application is idempotent and failures leave the
batch pending for retry.

`memory.shadow_extraction` completion telemetry reports both
`applied_reconciliations` and `deferred_reconciliations` without logging memory content.

## Phase 3: owner-DM memory and numeric relationship projection

Structured memory enters the response path in several deliberately different forms:

- In the owner's DM, `structured_owner_memory` contains all structured items owned by that user,
  regardless of origin realm/channel or disclosure. Relationship rows include their numeric evidence.
  Another user's items are never included.
- The owner's DM also receives `owner_relationship_profile`, which aggregates eligible active
  owner relationship evidence into the same sparse 1..4 axes used by the shared-space projection.
  This stabilizes the overall relationship/tone signal without replacing the concrete raw relationship
  memories already present in `structured_owner_memory`.
- In a shared space, active non-relationship items that resolve to `full` enter
  `structured_full_memory`. This covers facts/events/preferences/boundaries/tasks from the same
  disclosure space and items explicitly marked `global`, without opening hidden or reference-gated
  cross-space content.
- Relationship items that already resolve to `full` enter `structured_relationship_memory` with raw
  content and evidence. This keeps relationship evidence on its dedicated projection path.
- Cross-space `implicit` relationship items never expose raw `content`. Only a bounded
  `cross_space_relationship` evidence vector reaches the response model.

Each relationship extractor batch records sparse positive evidence on six 1..4 axes:
`familiarity`, `comfort`, `casualness`, `teasing_tolerance`, `support_openness`, and
`task_orientation`. The batch score is evidence from that batch, not a replacement global state. The
response layer considers at most the eight most recent eligible observations independently for
each axis and combines them with item confidence and exponential axis-local recency decay using noisy-OR.
An observation that carries no evidence for one axis therefore neither ages nor evicts evidence for that
axis. Repeated moderate observations can still accumulate gradually, while one batch cannot overwrite the
whole relationship profile.

A missing axis means "no positive evidence", not a negative preference. Current user instructions and
explicit boundaries always override the relationship profile. The model is explicitly forbidden from
reconstructing concrete past events, locations, names, or conversation content from numeric evidence.

- Store v4 retires `reference_gated` and migrates existing rows to `local`. Earlier edit history is retained.
- Extraction defaults ordinary memories to `implicit`. Only relationship evidence and allowlisted
  preference/boundary slots can influence a different space. Unmapped items contribute nothing.
  `cross_space_behavior` contains only tone_gentle, tone_playful, tone_reserved or praise_light.
  Sensitive annotations and stale slots cannot contribute. Low-risk stable address, language and
  format preferences may be global. Context-specific or private memories remain local.
- Raw structured-memory rows supplied to the response model include a coarse `scope_relation`:
  `current_channel`, `same_server_other_channel`, or `cross_space`. Raw guild/channel ids are not
  needed by the model for relevance decisions.
- A current-channel-only request is a relevance constraint, not a new privacy boundary. Privacy-safe
  FULL memory and implicit relationship projection remain available so stable preferences, boundaries,
  and relationship tone do not disappear because of a scope-classifier false positive. The response
  policy uses `scope_relation` to prevent other-channel fact/event/task memory from being presented as
  current-channel history.
- The structured-memory fields are included in routing context-size accounting so model routing sees the
  same dynamic context that request assembly will serialize.

Owner-DM reads and relationship aggregation, same-space relationship reads, and cross-space
relationship aggregation consume active items only, so superseded observations do not remain
simultaneously visible.
Deferred conflicts and relationship corrections or evidence-mismatched duplicates
remain active until a later policy can resolve them safely. New same-evidence
relationship duplicates are automatically superseded, preserving the profile
projection because only the existing observation contributes.

## Still out of scope

Shadow extraction still does not:

- replace `summaries` or `shared_summaries`,
- make structured memory the primary replacement for legacy summaries,
- automatically resolve `conflicts` or relationship-memory reconciliation proposals.

Those steps should be enabled incrementally after shadow classifications have been inspected against
real conversation data.

## Server community memory (P4)

The guild path runs independently of personal memory mode, including `legacy`.
Only completed explicitly addressed interactions enter channel-local extraction batches.
Sixteen new eligible turns trigger extraction after delivery. The stale sweep also
processes two or more turns when the oldest is at least eight hours old. Public
and private capture are processed separately. A proposal must quote original
turn text and pass source, author and consent checks again before storage.
An empty extraction is normal.

`SERVER_MEMORY_READ=off` disables server selection, member profiles and server
recall. Extraction, withdrawal, personal memory and administrator notes continue.
S0 carries verified administrator directives. S1 permits one ambient item at a
casual greeting and has a 72-hour cooldown. S2 and S3 return at most three
matched items and 800 content characters. Discord membership and source-channel
visibility are checked for each read. Thread sources use live parent-channel permissions. Public threads and forum posts can be shared when captured as public and still public. Private threads remain local and require live thread membership or Manage Threads in addition to parent read access.

Use `/memory sharing enabled:false` to withdraw contributions in the current
guild. Existing memories citing the contributor are deleted in full. Opting in
again allows future contributions. Message deletion removes dependent memories,
including those citing a delivered reply. The bot must receive the deletion event.
Guild managers can use `/server-memory show` and `remove`. The operator dashboard
links to server memory from Structured Memory and supports retraction and deletion.

The existing live evaluation CLI accepts `--target server` to run the six
guild extraction fixtures through the production parser and an isolated
in-memory database. It requires the selected provider's credentials:
`hina-memory-extraction-eval --target server --provider gemini --model MODEL`.

A correction or conflict between community claims holds both rows for operator
review. The dashboard shows the proposed relation and supports approval,
retraction and deletion. Server extraction operations are audited without
copying the claim text. A confirmed personal correction can suppress a related
claim about that same subject when the old sources or content match. The
correction text is never copied into the community memory.

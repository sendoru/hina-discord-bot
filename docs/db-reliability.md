# Database reliability contracts (#398)

These changes build on the explicit preparation and ownership contracts in
[db-initialization.md](db-initialization.md). Store remains the runtime connection's
transaction coordinator. The business schema, retention limits, memory modes and
purge deletion sets are unchanged.

## Shared summaries

Generation takes a coherent, short read snapshot of the previous summary row
(including absence) and the pending shared_calls. The read transaction ends before
the provider request. No SQLite write lock is held across network awaits.

`Store.commit_shared_summary` begins IMMEDIATE, checks the full prior summary row
and the exact ordered input prefix, then writes the summary and through_id in one
commit. Its cursor is a shared_calls ID, never a turns ID or Discord message ID.
The complete source rows are compared in memory; no source fingerprint is logged
or added to the schema. Appended calls remain pending if the original prefix is
intact. Unrelated scope writes do not invalidate the input. Existing pruning that
removes an input row does invalidate it; retention changes belong to #399.

Purge/forget, concurrent summaries, cursor changes and source edits cause a
`SharedSummaryConflict`, rolling back without resurrecting deleted content or
overwriting another result. A new call can retry the remaining pending input;
there is no immediate retry loop, scheduler or guaranteed retry time. Failed writes
and failed commits roll back the result and cursor. An already active transaction
is rejected without committing or rolling back its caller's work.

Completed, explicit empty-memory markers retain their existing semantics: commit
an empty summary and advance the cursor. Incomplete/whitespace-only output is
deferred as an error without cursor progress. Cadence below summary_every is an
ordinary skip. The bot's existing summary-failure handling reports partial_success
for a delivered turn whose summary failed or conflicted.

`memory.shared_summary_result` distinguishes committed, conflict, deferred, failed
and cancelled. Only a completed database commit emits committed. Provider response
completion alone is not a persistence claim. Telemetry includes bounded reason
codes and counts/sizes, never input rows, names, generated text or hashes.

The unconditional save_shared_summary setter remains available for explicit
compatibility/setup calls; production generation exclusively uses the guarded
snapshot/commit entrypoints, with no unconditional fallback. Repositories borrow
Store's connection and the guarded write SQL never commits internally. The existing
atomic_memory_batch source/cursor guard and extraction transaction are unchanged.

## Unified chatlog settings

all/direct/off/inherit retain their existing mode+capture-note representation.
SettingsRepository.replace_chatlog_override borrows Store's connection and owns
one short BEGIN IMMEDIATE transaction covering both values (or both deletes for
inherit). Failure of either SQL or commit rolls back both, including when the
second value failed after the first write. An already open transaction is rejected
without changing its caller's work. Standalone note and mode setters retain their
existing independent commit boundaries.

Discord and the Dashboard command executor perform their existing recent-buffer
clears after the database call returns, within the channel lock. Database failure
leaves buffers/hydration unchanged and does not report command success. A later
buffer error does not roll back the committed setting: it reports command failure
with the database already updated. Database state and ephemeral buffers are not
one distributed transaction. Channel/server/global clearing and hydration semantics
are unchanged; logical reset belongs to #400. Queue claim/action/finish commits and
InterruptedAdminCommand restart handling remain independent as before.

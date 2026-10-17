# Store boundaries: behavior-preserving first stage

Baseline: main `6d009da981fe13e77f3bd0733bfe9308395d3e46`.

The sections below record the behavior-preserving #394/#396 stages. Their constructor/
startup and inspection-opening descriptions are historical: #397 now replaces those
contracts with [explicit preparation and runtime startup](db-initialization.md).
The #398 reliability changes are documented separately in
[database reliability contracts](db-reliability.md).

## Ownership

- `core/store_schema.py`: existing Store DDL, compatibility migrations, and
  schema initialization order. It uses the supplied connection and does not open one.
- `core/conversation_repository.py`: conversation/name lookup, successful and
  failed turns, public calls and summaries, and existing per-scope retention.
- `core/memory_repository.py`: structured items, revisions, retraction,
  reconciliation candidates/proposals, and lifecycle transitions.
- `core/settings_repository.py`: notes, emojis, memory/chatlog overrides, runtime
  configuration, startup snapshots, and legacy settings migration writes.
- `core/store.py`: compatibility API, connection lifetime, extraction coordination
  and cursor queries, multi-table purge operations, and observed guild/channel metadata.

Repositories share the Store connection. They do not construct a Store, create
tables during construction, own the connection, or start background work.
SettingsRepository exposes an explicit runtime schema initializer, still called
only at the existing RuntimeSettings startup point. There is no generic CRUD
base class or ORM. Store retains its existing settings methods; RuntimeSettings and shared/Discord
chatlog policy use Store.settings for their persistence operations.

MemoryRepository receives a name resolver and a write-context factory.
The factory preserves standalone commits while joining Store's atomic extraction
batch without an inner commit. The resolver preserves late-bound Store name lookup.
Extraction commit still invokes Store.save_memory_extraction_cursor, including
failure injection and rollback behavior.

## Preserved contracts

This stage moves code; it does not change the database schema or product policy.

- Raw retention still uses history_turns and can discard unextracted rows.
- Successful turns and public calls still commit independently.
- Failed turns are not extraction inputs.
- Conversation reset still physically deletes its original table set and records
  the automatic-hydration cutoff.
- Memory purge retains the existing failure/batch records and manual settings.
- Store construction still performs migrations. RuntimeSettings startup writes
  and AdminDatabase initialization are unchanged.
- Dashboard keeps its separate read-only repository and command queue.
- The atomic extraction batch still checks the cursor and source fingerprint,
  commits items/cursor/journal together, and rolls back on failure.
- history_turns remains writable through the compatibility facade.

The preservation of undesirable behavior is deliberate: changing it in a later
PR makes data retention, reset semantics, and failure handling separately reviewable.

## Verification

`tests/core/test_store_contracts.py` covers retention scope, pruning failure
rollback, independent shared IDs, failure exclusion, cross-connection visibility,
the current purge contract, and schema/sequence continuity after reopening.
Existing extraction, lifecycle, reset, Dashboard, and Discord suites remain the
regression baseline. The normal CI matrix runs Python 3.11, 3.12, and 3.13.

## Follow-up boundaries

1. Move settings and maintenance SQL behind explicit APIs without changing behavior.
2. Separate read-only opening, schema migration, and runtime startup snapshots.
3. Review partial commits and stale public-summary writes as behavior changes.
4. Separate raw retention from prompt history limits and protect pending extraction.
5. Introduce persistent scoped context boundaries, then explicit history search and
   Dashboard archive views.

No production data migration is required by this code move. Future migrations
must preserve IDs, AUTOINCREMENT high-water marks, provenance, revisions, and
cursor semantics, and be rehearsed against a verified backup before deployment.

## Settings extraction (#396, first PR)

Baseline: main `b12807796395c36b71751410e41e8e110a160227`.

SettingsRepository borrows Store.db and never closes it. Domain parsing, effective
scope inheritance, runtime validation, override caches and Discord buffer
invalidation remain with their existing callers. SQL row ordering, note truncation,
error propagation and every existing commit boundary are preserved.

Legacy chatlog conversion commits the converted rows and marker together.
Runtime schema initialization, legacy RAG conversion, startup snapshot replacement,
and ordinary runtime writes remain separate operations in their original order.
Unified chatlog writes still make two commits; fixing that is #398.
The new failure-injection tests cover rollback of migration markers, RAG key
conversion, startup snapshots, and the runtime override cache after a failed write.

This PR requires no schema or operational data migration. Deployment rollback is
a code rollback; no rows need conversion. Existing initialization side effects,
retention and physical deletion policies remain in place.

The queue and maintenance portion of #396 follows below. #397 handles actual
read-only startup separation.

## Admin queue and registry ownership (#396, second PR)

Baseline: main `078d77154d19b9ac1ab74bc434fffbd723bbfd4b`.

`core/admin_commands.py` remains the queue SQL and validation boundary. Dashboard
enqueue owns a short-lived connection and always closes it. It requires an existing
queue table and does not bootstrap Store. Store owns the bot connection and exposes
claim, finish and interruption methods; Discord dispatch no longer passes Store.db.
The original connection-taking functions remain available for compatibility.

Each transition retains its own commit: enqueue; pending -> running; then running
-> succeeded/failed. Dispatch runs after the claim commits. Actions retain their
own Store/Registry commits and buffer effects; completion is a separate write.
Do not wrap the sequence in one transaction or invoke queue transitions inside an
extraction/other write transaction. Claim ordering is by id, with a conditional
pending update. Duplicate request_id returns the existing id without replacing
its original payload. Invalid stored payloads fail and are skipped in order.

Cancelled dispatch or failed completion can leave a running row even after an
action committed. Startup marks these failed with InterruptedAdminCommand and
scrubs the payload; it never retries the action. This is not an exactly-once queue.
Normal finish and invalid-payload failure also scrub payloads. Dashboard queries
remain in its read-only AdminRepository; writes only enqueue commands.

`AdminDatabase` continues to own a separate connection, created/closed by LLM or
the standalone runtime migration. InstructionRegistry and RuntimeKnowledgeRegistry
borrow that database and keep their validation and SQL. Their transaction() calls
join an enclosing AdminDatabase.transaction(): only its outermost scope starts
BEGIN IMMEDIATE and commits/rolls back. This is not participation in Store's
transaction or an arbitrary raw BEGIN. No additional Repository/Service is needed.
Constructor DDL and migration dry-run initialization effects are unchanged (#397).

## Offline maintenance boundaries and direct SQL exceptions

`tooling/maintenance_db.py` contains only the existing connection opening and small
table/count queries formerly borrowed from reset_analysis_data. It never constructs
Store, migrates schema, begins a transaction or commits. A successful open transfers
connection ownership to the CLI caller; each caller closes in finally. Its existing
read/write open, busy_timeout=1000 and secure_delete=ON also apply to plan callers.
It is not a universal connection factory or the future strict read-only API.

| Owner | Inspect / plan | Backup / execute and transaction responsibility |
| --- | --- | --- |
| flush_pending_memory | inspect_pending owns a mode=ro connection; pending_scopes borrows either that or Store.db and preserves oldest/id scope ordering | run_flush backs up before Store/RuntimeSettings construction; then the existing worker commits each extraction batch through Store.atomic_memory_batch, not one transaction for the entire flush. Worker and Store are closed by run_flush. |
| reset_conversation | Owns an existing-DB connection and builds counts/pending plan | Verifies backup first, opens execution connection, starts BEGIN IMMEDIATE and rechecks plan; name preservation, epoch DDL, the four original table deletes and epoch insertion commit together. Failure rolls them back. Checkpoint follows commit. |
| migrate_conversation_reset_epochs | Closes inspection connection before backup; current schema is a no-op without backup | After backup, owns a new connection, locks and rechecks columns. ensure_epoch_schema borrows this transaction; it never commits. DDL commits/rolls back in the caller, followed by checkpoint. |
| reset_analysis_data | Builds the existing raw/preserved/pending/log-file plan | No automatic backup is added. Owns execution connection, locks and rechecks pending work; raw deletes and observability epoch commit together. Checkpoint and log-file deletion remain after commit; filesystem effects are not atomic with DB changes. |
| maintenance_backup | Validates source and required tables without Store initialization | Owns source, destination and read-only verification connections. Uses SQLite backup (including WAL), integrity/count checks and existing hash/no-overwrite publication. Verification connection is explicitly closed before publication or failure cleanup. |

Direct SQL intentionally remains in these owners: inspection must not bootstrap
Store; backup uses SQLite's connection API; offline deletion and epoch DDL must stay
inside their caller's write transaction. No helper may introduce an independent
commit into reset/epoch execution. Missing-table handling, deletion/preservation
sets, pending rules, plan output, ID sequences, source provenance and cursor meaning
are unchanged. Pending queries that differ in legacy support or purpose are not
merged merely because their SQL looks similar.

Store and RuntimeSettings construction during a flush still perform their existing
schema/legacy conversion and startup-snapshot writes, after the backup. Removing
these side effects, changing plan/dry-run opening, or introducing versioned migrations
belongs to #397. Stale shared summaries and compound settings commits remain #398;
retention, logical resets and history search remain #399-#401.

Contract coverage includes malformed queue payloads/FIFO, separate connection
visibility, failed/cancelled dispatch and failed finish followed by restart without
retry, registry outer rollback, backup connection closure on success/failure, and
post-backup conversation/schema changes rejected before execution. Existing legacy
fixtures, extraction rollback/provenance and purge/reset preservation tests remain
the regression baseline. This stage needs no operational schema/data migration.


## Explicit initialization (#397)

See [DB initialization](db-initialization.md) for current APIs, supported legacy
shapes, schema history, backup/restore and deployment commands. Constructors bind
prepared connections; RuntimeSettings loads only; actual bot startup owns base
snapshot capture. Inspect/plan/dry-run use read-only target connections. DDL and
legacy transformations run only in explicit preparation, with one owned transaction
and verified backups for existing files. Queue dispatch, extraction atomicity,
purge/reset table sets, retention and compound runtime settings writes are preserved.

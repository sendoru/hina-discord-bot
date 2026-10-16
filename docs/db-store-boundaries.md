# Store boundaries: behavior-preserving first stage

Baseline: main `6d009da981fe13e77f3bd0733bfe9308395d3e46`.

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

Remaining #396 work is a separate PR: make the admin command queue's connection
ownership explicit, retain AdminDatabase/InstructionRegistry/RuntimeKnowledgeRegistry,
and document/refactor maintenance inspect/plan/backup/execute boundaries without
changing their current transaction or dry-run behavior. #397 handles actual
read-only startup separation. Do not introduce a generic repository base class,
a universal connection factory, or an ORM merely to remove direct SQL.

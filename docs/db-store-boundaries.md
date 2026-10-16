# Store boundaries: behavior-preserving first stage

Baseline: main `6d009da981fe13e77f3bd0733bfe9308395d3e46`.

## Ownership

- `core/store_schema.py`: existing Store DDL, compatibility migrations, and
  schema initialization order. It uses the supplied connection and does not open one.
- `core/conversation_repository.py`: conversation/name lookup, successful and
  failed turns, public calls and summaries, and existing per-scope retention.
- `core/memory_repository.py`: structured items, revisions, retraction,
  reconciliation candidates/proposals, and lifecycle transitions.
- `core/store.py`: compatibility API, connection lifetime, extraction coordination
  and cursor queries, multi-table purge operations, and remaining settings SQL.

Repositories share the Store connection. They do not construct a Store, create
tables, own the connection, or start background work. There is no generic CRUD
base class or ORM. Existing callers continue using Store.

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

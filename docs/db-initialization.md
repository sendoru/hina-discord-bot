# Explicit SQLite preparation and runtime startup (#397)

Database preparation is now an explicit operation. Opening Store, AdminDatabase,
RuntimeSettings or an LLM no longer creates/migrates an application database.
Schema preparation is separate from JSON content import and from actual bot startup.
Store remains the compatibility facade and extraction/purge transaction coordinator.

## Upgrade or first installation

Stop the bot, Dashboard and every other DB writer before applying preparation.
Use the same DATABASE_PATH as the bot. The preparation CLI reads `.env.local` and
`.env` with the same environment precedence as Settings.load, without requiring a
Discord token or provider key merely to inspect a database.

```sh
uv run hina-prepare-db --dry-run
uv run hina-prepare-db --yes --offline-confirmed
uv run hina-bot
```

Dry-run is the default. Missing paths are reported as a new-database plan without
creating a file or parent directory. A real application creates a new file with
exclusive creation, or backs up an existing file before changing its schema.
`--component store`, `--component settings`, and `--component admin` may be repeated
for explicit partial preparation; the normal bot needs all three, which is the default.
An already current database is a no-op without another backup or snapshot write.

For Docker Compose, keep the writers stopped while preparing the same volume:

```sh
docker compose stop
docker compose build
docker compose run --rm hina hina-prepare-db --dry-run
docker compose run --rm hina hina-prepare-db --yes --offline-confirmed
docker compose up -d
```

`--offline-confirmed` is the operator's assertion, not a process supervisor or a
lock against an independent process starting later. The write phase rechecks the
pre-backup database fingerprint under BEGIN IMMEDIATE and refuses changed input.
Do not run a second bot against the same file.

## Schema history and supported legacy data

`schema_migrations(component,version,name,applied_at)` is one small history table.
The initial ordered areas are store, settings and admin, each with baseline version
1. A fixed preparation function per area is sufficient; there is no generic
migration framework, ORM, Repository base class or independent repository connection.
Future revisions must add ordered, explicit migrations rather than relabel old data.
The existing `admin_migrations` table still records JSON content imports and is not
used as schema history.

Unversioned databases are inspected before adoption. Owned column definitions,
primary/unique keys (including sort/collation), CHECK expressions, AUTOINCREMENT
and required indexes are checked. Unknown/future history and a recorded version
that disagrees with the real structures are rejected. An unrelated nonempty SQLite
file is not stamped as Hina. Extra unrelated tables are preserved.

Supported legacy shapes include the existing turns optional columns, memory_items
without lifecycle/evidence/name fields, the known pre-retraction CHECK, legacy
memory history/proposal tables without their current CHECK/unique constraints,
legacy personal summaries, chatlog conversion markers, and the legacy RAG key.
Unknown columns, constraints or dependencies that a rebuild would discard cause
an error. Arbitrary damaged/partial schemas are not repaired automatically.
The compatibility fixtures exercise these paths on actual files.

Known memory table rebuilds copy complete rows and restore sqlite_sequence high
marks, including deleted highest IDs. Provenance JSON, revision/history, proposals,
manual settings and cursor meanings are retained. Existing structured cursors win
when legacy summaries are converted; the old summary table is removed according to
the existing migration contract, with its complete original available in the backup.
Malformed legacy data that violates the supported target constraints aborts and
rolls back; it is never quietly dropped to complete an upgrade.

Reset epoch audit schema variants remain owned by the existing reset/epoch CLI.
Ordinary opening or bot startup does not drop old epoch counters. Epoch DDL still
joins the reset caller's transaction and does not commit independently.

## Transactions and backup/restore

The write phase owns one transaction covering selected schema changes, existing
legacy data transformations and their history records. Initializers execute
individual statements and never use executescript or make inner commits.
Failure, including interruption, rolls back the complete preparation transaction.
Journal mode configuration runs after that commit; failure there is reported as
committed schema with unsuccessful WAL configuration, not a successful rollback.

Schema preparation backups support inspected admin-only/legacy schemas without
first adding runtime tables. They use SQLite backup, integrity_check and a streamed
schema/row/sequence fingerprint comparison, followed by a file SHA-256. The source
connection is read-only; verification/source/destination connections close explicitly.
Existing reset/flush backups retain their stricter runtime-table requirements.
Backups remain private, non-overwriting files (0600) in a separate directory (0700).
Fingerprinting performs full reads; its cost depends on actual database size.

Before deployment, verify the printed backup path/hash. To recover, stop all writers,
preserve the failed/new database, and restore a verified full snapshot into a separate
location first. Check integrity, schema, original rows, ID high marks, provenance,
revisions, cursors and settings. Avoid mixing a restored database with stale WAL/SHM
files. The automated recovery test performs a real SQLite backup restore into a
second file and verifies/reopens it; a hash comparison alone is not a restore rehearsal.

A source rollback to pre-#397 code is not treated as a schema downgrade. The old code
may reopen the current data because business table formats remain compatible, but
it will run its old constructor migrations and snapshot writes again. Use a verified
backup restore if an actual schema downgrade/recovery is required. No automatic
reverse migration or production DB execution is included in this change.

## Connection and object ownership

- `open_readonly(path)` uses an encoded SQLite URI with mode=ro/query_only; it never
  creates the target database, changes journal mode, checkpoints, or initializes schemas.
- `open_existing(path)` uses mode=rw and cannot recreate a missing file after a path
  check. Callers own and close the returned connection.
- `Store.open_existing(path)` validates the store/settings areas and sets the existing
  writer WAL/secure_delete options. `AdminDatabase.open_existing(path)` validates the
  admin area and retains a separate writer connection; readonly=True omits writer
  PRAGMAs. Neither upgrades schemas or writes a startup snapshot.
- `Store.create(':memory:')` / `AdminDatabase.create(':memory:')` explicitly prepare
  isolated databases. With a file path these factories create only a new file or
  open an already prepared one; existing legacy files must use the backed-up CLI.
- `Store(connection)` / `AdminDatabase(connection)` transfer ownership of an already
  prepared sqlite3 connection and perform no SQL. Old path constructor calls fail
  early with a direction to use create/open_existing.
- Repositories and Registries borrow their owning database. Domain write boundaries,
  AdminDatabase's nested import transaction, and Store.atomic_memory_batch's source/
  cursor checks and no-inner-commit guarantee remain unchanged.
- An LLM optionally receives an explicitly prepared admin_db and takes its lifetime
  ownership. With no database supplied, Registry(None) has its existing empty-read/
  disabled-write behavior. It never opens settings.db_path implicitly.

## Actual runtime start and snapshots

`discord/runtime_entry.py` opens prepared Store/admin connections, loads settings,
constructs the provider/LLM/bot, then explicitly captures the base startup settings
before the actual run. Failed initialization closes the resources already opened;
snapshot insertion failure preserves the previous complete snapshot and prevents run.

RuntimeSettings construction/reload reads overrides only. Snapshot values are still
the startup environment/base values, not effective DB overrides. Only a deliberate
bot start captures them; reconnects, CLI inspection, maintenance flush, JSON import
and evaluation do not replace them. Configuration inheritance, validation, caches,
Discord buffer invalidation, and existing settings commits are unchanged. Compound
chatlog write atomicity and stale shared summaries remain #398.

## Maintenance, JSON import and evaluation

All maintenance plans use read-only target connections. Actual reset/epoch execution
retains its original backups, write lock, revalidation, atomic DDL/deletion sets and
post-commit checkpoint. reset_analysis_data still commits DB changes before deleting
log files and does not acquire an automatic backup as part of this refactor.

Flush requires a prepared store/settings schema. It keeps the pre-extraction backup
and existing batch-level extraction commits, but no longer migrates schema or
captures runtime_config_startup. If preparation is needed, run the explicit CLI
before flush; do not broaden extraction or retention as part of upgrading.

The legacy JSON import command retains its payload validation, duplicate/replace
rules and joint Registry rows/content-marker transaction. Dry-run copies an existing
source via a read-only connection into memory and runs the real Registry validators
there; a missing target is simulated in memory. The target file/directory/schema/
startup snapshot remains untouched. Real import requires stopped writers:

```sh
uv run hina-migrate-runtime --dry-run
uv run hina-migrate-runtime --offline-confirmed
```

Real import takes a verified backup of an existing target, explicitly prepares the
admin area if needed, then revalidates and imports. Schema preparation and content
import are separate commits; a failed content import does not pretend to undo a
previously committed schema preparation. Source JSON files are still not deleted.

Evaluation uses isolated in-memory admin data as well as an isolated conversation
Store. `hina-eval --admin-database PATH` supplies an optional read-only source copied
into the evaluation memory DB; explicitly configured DATABASE_PATH can also serve
as this source. It never upgrades or writes the source. The prompt-injection script
supplies an explicit isolated admin DB too. Evaluations that do not construct an LLM
are not forced through database initialization. No live provider requests are needed
for the initialization/cleanup contract tests.

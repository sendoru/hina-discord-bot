# Explicit SQLite preparation and runtime startup

Runtime accepts the recorded current schema only. It never adopts an unversioned
file, infers a legacy shape, adds columns, or converts settings. Store remains the
connection owner/transaction coordinator; repositories still borrow its connection.

## Current v1 baseline or a new installation

Stop the bot, Dashboard and every other writer before applying preparation. Use
the same DATABASE_PATH as the bot; inspection defaults to dry-run and does not
require Discord/provider credentials. `.env.local`/`.env` precedence is unchanged.

```sh
uv run hina-prepare-db --dry-run
uv run hina-prepare-db --yes --offline-confirmed
uv run hina-bot
```

The inspected operational schema is baseline v1. Its business tables, indexes,
constraints and existing rows are unchanged. An unversioned file in that exact
known shape is registered in `schema_migrations(component,version,name,applied_at)`:
store/settings/admin each start at `*-baseline-v1`. Fresh DDL and the inspected
historical ALTER spelling are the two explicitly accepted store signatures.
Settings/admin each have one signature. The ledger is separate from JSON content
import's `admin_migrations`; there is no second version authority.

A current file is a no-op: no backup, journal reconfiguration or snapshot rewrite.
Missing paths are simulated in memory for inspection; a real new file is created
exclusively. `--component store`, `settings`, `admin` may select complete areas.
Partial/older structures within an area are refused rather than repaired. Runtime
requires the selected areas' complete recorded current history and metadata.

For Docker Compose, keep writers stopped and use the same volume:

```sh
docker compose stop
docker compose build
docker compose run --rm hina hina-prepare-db --dry-run
docker compose run --rm hina hina-prepare-db --yes --offline-confirmed
docker compose up -d
```

`--offline-confirmed` is an operator assertion, not a process supervisor. The
preparer compares the pre-backup data fingerprint under BEGIN IMMEDIATE and
rechecks the structures/history. A separate process must not restart as a writer.

## Older unversioned databases: a separate offline command

Normal preparation refuses old columns/constraints, old personal summaries and
unconverted chatlog/RAG data. Only the independently invoked converter loads their
structure analysis and transformation code:

```sh
uv run hina-upgrade-legacy-db --database /path/to/offline-copy.sqlite3 --dry-run
uv run hina-upgrade-legacy-db --database /path/to/offline-copy.sqlite3 --yes --offline-confirmed
```

The path must already exist; the converter has no environment-derived default.
It first inspects read-only, then verifies a private full backup before writing.
Unknown columns/constraints/indexes, custom views/triggers and incoming foreign
keys that could be broken by a known table rebuild require manual review. It is
not a repair tool for arbitrary or corrupt databases.

Supported fixtures retain the known turns optional columns, memory lifecycle/
evidence/history/proposal upgrades, old personal-summary cursors and chatlog/RAG
conversion. Existing structured cursors win. The old personal summary is removed
by its existing conversion contract; its complete original remains in the backup.
The converter canonicalizes known table/index SQL to the fixed v1 spelling so
runtime does not need every historical DDL spelling. It preserves every copied
column value, ID, revision, provenance, timestamp and AUTOINCREMENT high-water,
including deleted highest IDs. Unsafe dependencies are refused before mutation.
All conversion/DDL/history writes share one transaction; SQL/commit failure rolls
back the complete operation. A registered current file is a no-op.

## Ownership, startup and evaluation

- `core/db_ddl.py`: unchanged business DDL plus fixed v1 ownership/signatures.
- `core/db_schema.py`: current history/metadata validation and fresh creation only.
  Exact sqlite_master metadata includes attached unexpected indexes/triggers.
  No SQL normalization changes literal/constraint meaning; an unknown spelling is
  refused conservatively, even if it might be semantically equivalent.
- `tooling/db_migrations.py`: explicit baseline and ordered next-version steps;
  `tooling/prepare_database.py` owns file backup/connection/journal operations.
- `tooling/legacy_database.py`: old format analysis/conversion. Runtime and normal
  preparation never import this module. See [baseline review](db-baseline-v1.md).
- `open_readonly` never creates/migrates/configures writer PRAGMAs. `open_existing`
  cannot recreate a missing file; callers close owned connections.
- Store/AdminDatabase constructors bind prepared connections. Their open_existing
  factories validate the current schema; their create factories create only new
  areas with current DDL. They do not upgrade an existing file.
- AdminDatabase.copy_to_memory validates an existing source's current admin schema
  before a readonly copy, then revalidates the copied snapshot. It initializes only
  a new isolated memory DB. Prepare an older source offline before evaluation.
- RuntimeSettings construction/reload reads overrides only. Actual runtime_entry
  constructs Store/admin/provider/LLM/bot, then captures the base startup snapshot.
  Failed initialization closes resources; failed snapshot preserves the old one.

The JSON import command remains `hina-migrate-runtime`; source JSON validation,
registry transactions, backup, dry-run isolation and content markers are separate
from schema preparation. JSON dry-run explicitly prepares a missing/current admin
area on its disposable copy, matching real import without touching the source.
Legacy admin structures still require the separate converter.
Flush/reset/epoch/analysis maintenance retain their own
backup and commit contracts. Ordinary opening does not change audit epoch schemas.

## Backup, restore and later versions

Schema backups use SQLite backup (including committed WAL), integrity_check,
streamed schema/all-row/sequence fingerprints and file SHA-256 verification.
Backup directories/files remain 0700/0600 and non-overwriting. Full data reads are
offline operations; runtime validates schema metadata only.

To recover, stop writers and restore a verified snapshot to a separate file first.
Check integrity, all rows, ID high marks, revisions, sources, cursors and settings.
Do not mix restored files with stale WAL/SHM. Rehearsals perform a real restore,
not only a checksum comparison. A missing WAL in a supplied snapshot cannot prove
that the original server had no uncheckpointed commit when it was copied.

Future changes must append named v1→v2 steps and immutable version signatures;
see the concrete extension procedure in [db-baseline-v1.md](db-baseline-v1.md).
No v2 feature, retention/reset/search change, schema downgrade or production apply
is included here. Old code may run constructor migrations/pruning and is not a
safe schema rollback; use the verified recovery procedure with writers stopped.

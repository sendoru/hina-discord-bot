# Baseline v1 and offline migration separation (#395)

This change targets the inspected operational business schema already accepted
by #397–#398. It changes no business table/column/index/constraint or retention
policy and implements none of #399–#401. Production application is not performed.

## Three concrete paths

1. **Runtime/open existing:** validate the current contiguous history and exact
   owned metadata. No legacy imports, reference SQLite DB, CHECK parser, ALTER,
   setting conversion or implicit baseline registration.
2. **Prepare/current baseline:** readonly inspection → verified backup →
   BEGIN IMMEDIATE → fingerprint/plan recheck → baseline/explicit version steps →
   current validation → commit. WAL configuration remains a separate post-commit
   step whose failure must not be reported as a rolled-back schema transaction.
3. **Convert older input:** independently invoked `hina-upgrade-legacy-db` retains
   only the legacy column/constraint/data contracts exercised by old fixtures.
   It rejects unsafe rebuild dependencies and converts to fixed v1 SQL inside one
   transaction, including ledger writes. Runtime/normal prepare do not import it.

Fresh Store/admin DBs execute current DDL directly. Copy-to-memory validates an
existing admin source before copying; a test/evaluation no longer serves as a
hidden legacy-upgrade entrypoint. New memory DBs still initialize explicitly.

## Fixed baseline evidence

`V1_TABLES` and `V1_SIGNATURES` in db_ddl.py describe owned metadata only. Hashes
cover type/name/table/SQL of owned tables and all attached indexes/triggers,
including implicit unique indexes. They do not include data, sequence values,
Discord IDs or private settings. The operational schema's SQL-only fixture is
`tests/fixtures/db-operational-v1.sql`; it contains no production rows.

Store has two known metadata signatures: fresh DDL and the actual inspected
historical ALTER spelling. Settings/admin/ledger each have one. Baseline tests
reproduce those signatures without a production DB. Exact comparison deliberately
refuses unknown SQL spelling rather than inferring arbitrary SQL equivalence.
Unrelated physical reset/analysis audit tables remain outside schema ownership;
rebuild dependency checks still inspect external views/triggers/foreign keys.

Baseline registration accepts the known unversioned schema with completed chatlog
conversion and no legacy RAG key. It changes only the ledger. A known versioned
schema with extra unique index/trigger, changed CHECK literal, missing index or
wrong history is refused. Legacy fixtures belong to the offline converter tests,
not to the runtime's accepted-shape list.

## Adding the next real version

No v2 migration ships. For a future actual change:

1. Keep v1 schema definitions/signatures immutable; add the new current DDL and
   the known resulting metadata signature. The legacy converter must continue to
   target frozen v1, rather than accidentally adopting future DDL.
2. Append the component's unique name to `HISTORY`; register the resulting
   version signature in `SCHEMA_SIGNATURES`. Keep historical signatures so the
   preparer can validate each supported starting version. Runtime requires latest.
3. Write one named function taking the existing connection. Append exactly its
   `(component, next_version, function)` to `UPGRADES` in db_migrations.py. It must
   not open another connection, commit, or use executescript.
4. The coordinator executes every missing version in numeric order and inserts
   that version's history after its SQL. All steps share one transaction. Missing
   steps, gaps/unknown history, drift, changed source and SQL/commit exceptions
   fail safely. Fresh current creation records current history after creating the
   current DDL, rather than replaying old data conversions.
5. Test starting/current/fresh shapes, repeated execution, step/commit failure,
   source race and real restore. Keep ID/sequence/revision/provenance comparison.

The fixed history lists, a small tuple of Hina-specific functions and metadata
signatures are sufficient. There is no filesystem discovery, migration plugin,
ORM, abstract repository, schema DSL or dependency-injection migration framework.
The ordered-step test uses synthetic v2/v3 indexes only; they are not application
schema changes and are never installed outside isolated test databases.

## Verification and limits

The operational attachment is used only in private disposable copies. Its original
file hash/size/mtime/permissions and lack of new sidecars are checked again after
verification. Preparation, repeat/no new backup, actual restore, complete original
row/sequence hashes and schema/ledger/commit failure rollback are exercised.
No private database or content is committed or sent to CI. Public CI uses the
metadata-only SQL fixture and synthetic rows.

Full reports of source/runtime line counts, initialization SQL calls and final CI
results accompany the PR. Moving safe legacy recovery code offline can increase
repository-wide code while reducing the code/branches loaded by runtime. Do not
claim a whole-bot dependency reduction merely because schema code no longer
imports config/scope helpers: other bot features still need those modules.

The supplied snapshot does not establish completeness of the original server's
WAL, repair already missing source messages, verify real Discord/provider calls,
or simulate power loss/operating disk failures. None of those are deployment
approvals. #407 remains a separate Draft integration PR against main.

## Measured change against integration/db395 (4691884)

Physical Python lines include comments/embedded SQL and exclude tests/docs.

| Measure | Before | After |
| --- | ---: | ---: |
| core/db_schema.py | 416 | 109 |
| Runtime schema files (db_schema + db_ddl) | 2 / 658 lines | 2 / 400 lines |
| Functions in runtime db_schema | 14 | 7 |
| Whole src Python files / lines | 153 / 33,941 | 155 / 34,209 |
| Store/Admin existing-file open+close, target SQL calls | 150 | 15 |
| Same cold-process probe, reference memory DB calls | 151 (39 DDL) | 0 |

The whole source grows by 268 lines. Two offline modules account for explicit
preparation/version steps and the separately invoked safe legacy converter.
The legacy tool is 401 lines including its CLI, validation, canonical copy,
sequence/dependency guards and transactional conversion. Those guards are retained
because recovery safety remains necessary outside runtime. Fixed signatures add
49 metadata lines alongside unchanged business DDL. No application class/layer or
repository/service abstraction is introduced.

The probe opens/closes Store and AdminDatabase on separate disposable copies of
the same prepared operational snapshot, using Python 3.13.15 / SQLite 3.53.1 and a
fresh process per revision. It is a schema initialization SQL count, not a whole
Discord startup benchmark or a steady-state message performance claim. Target
connection count remains two. Schema code no longer traverses config/scope/routing
helpers for legacy conversion; the rest of the bot still uses those files.

Private snapshot preparation changed only schema_migrations. Complete original
table rows, original table/index SQL and all nine sequence entries were preserved;
repeat preparation left bytes unchanged and created no new backup. A restored
backup reproduced the full original logical state and prepared/reopened normally.
Three SQL/commit failure stages rolled back with verified backups intact. Separate
private tests on existing rows passed all 23 extraction/summary/settings/startup,
source-race and WAL-backup contracts, with network/provider requests disabled.

Final local verification: Ruff and version check passed; Python 3.13.15 full suite
**1,514 passed + 3 subtests**, with 8 Playwright browser tests skipped locally.
The existing Starlette/httpx deprecation warning remains. The branch CI matrix
(Python 3.11/3.12/3.13) and separate browser job verify the same commit remotely;
private attachment data is excluded from those runs.

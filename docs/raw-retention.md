# Raw conversation retention (#399)

## Policy

`HISTORY_TURNS` still selects the latest automatic prompt history (default 12), with
the existing character budgets, ordering and disclosure rules. It no longer deletes
raw data. Public context still returns at most four scopes and two recent user
messages per scope. No P4 server shared-memory feature is introduced.

`RAW_RETENTION_TURNS` is a startup-only setting, default **1000**, valid from
`HISTORY_TURNS` through 100000. It is a target for the latest rows **per scope, per
table** (`turns`, `shared_calls`, `failed_turns`), rather than a database/disk quota.
There is no age-based expiry or automatic VACUUM.

- A successful turn outside the latest target is eligible only when its ID is at
  or below that scope's `memory_extraction_cursors.through_id`.
- A public call uses its own `shared_summaries.through_id`. Public call IDs and
  successful turn IDs are different sequences and are never compared to each other.
- Missing cursor/summary means zero: all corresponding inputs remain protected.
  Pruning does not create/advance cursors or delete memories, revisions or sources.
- Failed turns are audit data, never extraction or summary input. Their finite
  target has no cursor exemption. They still do not count for `seen()`.
- On each source insertion, at most 1000 eligible old rows of the affected table
  are deleted inside the insertion's transaction. Completed turn + public projection
  + both cleanups still commit/roll back together. Hydration's standalone `add()`
  retains its existing transaction. Cleanup does not call providers.
- Processing a backlog does not itself clean up; the next source write in that
  scope does. An inactive scope can remain above the target. Large recovered tails
  can take several writes to converge. There is no global cleanup job in this change.

Pending sources may exceed the target indefinitely. Disk exhaustion still fails
ordinary writes; this change adds neither a hard quota nor a silent discard/skip
fallback. Monitor backlog age, count and available disk. A warning without content
or identifiers appears at the first overflow and every target-sized increase.
Warnings can precede an eventual enclosing-transaction rollback; inspect the report
for committed state. An operator must resolve disabled modes/provider failures,
provide capacity, or choose an existing explicit deletion operation.

## Modes, batches and explicit deletion

`off`/`read_only` retain the existing runtime gates: they do not newly capture
successful/failed turns or run automatic memory writes. Existing pending data
remains pending and protected when a mode changes; re-enabling writes allows normal
processing. Nothing advances a cursor just to make pruning possible.

Structured extraction remains bounded by `STRUCTURED_MEMORY_EVERY`. Existing
`hina-flush-memory` uses a one-row existence check, bounded batches, mode rejection,
progress checks and a verified offline backup. It handles structured extraction
only. Public-summary snapshots now read at most `HISTORY_TURNS` oldest pending
calls, with the existing exact-prefix guard on commit; appends remain pending.
Below-cadence tails remain protected. There is no new automatic retry loop,
background public summary worker or model invocation from inspection/cleanup.

Explicit forget/purge/reset contracts take precedence over automatic protection.
The existing offline reset still refuses structured pending unless the operator
explicitly chooses `--discard-pending`, and still archives/backups its original
table set. [Offline reset](offline-conversation-reset.md) and analysis-data deletion
remain separate operations; their permissions and preservation sets are unchanged.

`seen(message_id)` still checks retained successful turns only. Keeping more raw
turns therefore extends deduplication until expiry or explicit deletion. There is
no new deduplication table, failed-turn suppression or exactly-once delivery claim.
Dashboard conversation/source lookup and `chat_history_records()` can find retained
rows using their existing boundaries. A memory source reference does not pin a
processed original forever. Already absent sources are not reconstructed.

## Read-only inspection

```sh
hina-retention-report --database /path/to/backup.sqlite3 --raw-retention-turns 1000
```

This command opens an existing, prepared database read-only, with no `Store`
initialization, provider request, migration, pruning, checkpoint or settings write.
The report streams per-scope aggregate rows, emitting only table totals, scopes,
pending/disabled-write counts, oldest pending age, excess counts and page/file/WAL
sizes. It prints no scope/user/message IDs, names or text. Flags are explicit;
defaults use exported `DATABASE_PATH`/`RAW_RETENTION_TURNS`, then built-in defaults.
It does not load `.env` or infer another running process's configuration.
Use the deployment's actual retention target when interpreting excess counts.

## Measurement and initial target decision (2026-11-05)

Basis: main `002a80e`, a user-supplied standalone SQLite snapshot, and isolated
synthetic fixtures. The supplied file had no WAL sidecar. It was opened immutable
and read-only to make a separate private copy; all subsequent writes below used
copies/synthetic databases only. No operational DB was accessed or changed.
Only aggregates are published; no database, backup or source text is committed.

The snapshot was 7,708,672 bytes (446 freelist pages). It contained:

| Table | Rows | Scopes | Pending | Maximum rows/scope |
| --- | ---: | ---: | ---: | ---: |
| turns | 15 | 8 | 11 | 6 |
| shared_calls | 1 | 1 | 1 | 1 |
| failed_turns | 0 | 0 | 0 | 0 |

There were 216 memory items, 217 structured cursors and 691 debug archive records
dated October 22–November 5. No pending scope was write-disabled. Successful/archive
payloads averaged roughly 5.2k/5.8k characters respectively; archived payloads averaged
6082 UTF-8 bytes (maximum 11939). The largest observed archived scope/day was 13
records. These are **retained observations after prior pruning/reset**, not a full
traffic series or a reliable upper bound on arrivals. Of 505 unique stored memory
source IDs, 19 were present in turns/archive and 486 were absent from this snapshot;
this implementation does not claim to recover them.

Decision: use a configurable **1000-row target**, retaining the current 12-turn
prompt limit, and defer a 30-day expiry. This gives substantially more source/debug
room with measured single-scope payload costs on the order of several MB, without
adding a second expiry criterion that could silently change inactive conversations.
It is an initial default supported by measured cost and the source-preservation
requirement, not a promise of 30 days or a production capacity forecast. Revisit it
with an unpruned traffic series and disk/backlog observations after deployment.

Synthetic fixtures had 100 scopes and respectively 1k, 10k and 100k successful
turns plus equally many public calls. Each turn had a 5762-character ASCII payload
(user/reply/context/provenance), and each public call 128 characters. Reported times
are the median of seven sequential local warm runs on Python 3.14 / SQLite, in ms;
they are not production latency guarantees.

| Operation | 1k | 10k | 100k |
| --- | ---: | ---: | ---: |
| Automatic history | 0.074 | 0.049 | 0.042 |
| seen | 0.005 | 0.004 | 0.004 |
| Explicit source lookup | 0.064 | 0.027 | 0.026 |
| Structured pending, limit 4 | 0.037 | 0.023 | 0.021 |
| Shared snapshot, limit 12 | 0.030 | 0.021 | 0.020 |
| Public context, four scopes | 0.076 | 0.044 | 0.045 |
| Completed source write, processed cursor | 0.074 | 0.125 | 0.335 |
| Stale scope scan | 2.744 | 22.156 | 281.388 |
| Aggregate retention report | 5.150 | 33.741 | 480.597 |
| Dashboard first 50 rows | 7.685 | 59.094 | 577.736 |
| Dashboard 50 rows at halfway OFFSET | 15.591 | 140.699 | 1526.591 |
| Dashboard message-source lookup | 0.362 | 0.362 | 0.358 |

Checkpointed fixture files were 6,787,072 / 65,310,720 / 651,702,272 bytes. Bulk
fixture insertion temporarily left WAL files of about 6.6 / 65.5 / 655.3 MB; do not
budget only the main file. `EXPLAIN QUERY PLAN` used `turns_scope(scope,id)` for
history/pending and the existing unique `message_id` covering index for `seen()`.
No new index/schema migration was justified at the observed snapshot size. Global
scans and Dashboard UNION/sort/deep OFFSET still grow with retained rows: at 100k,
deep pages took about 1.5s. This is a documented scaling limit, not a fast-pagination
claim. No archive/FTS/cache subsystem is added.

## Copy/rollback rehearsal and deployment boundary

On the supplied copy, integrity checks passed, all structured/shared cursors were
nonnegative, and the three raw-table sequences were at least their live maximum IDs.
An SQLite backup was restored to a new file. Full streamed schema/row/sequence
signatures matched, covering memories, source metadata, settings and cursors as well
as raw rows. Current startup accepted the schema without migration.

In another restored copy, 1010 synthetic sources were appended to an existing scope.
Original pending IDs survived above the target, automatic history stayed at 12,
retained-source deduplication/lookup worked, and off/read_only preserved the tail.
Fake/local guarded commits drained structured inputs in four-row batches and public
inputs in batches of at most 12. The next source write converged turns to 1000 while
keeping a new pending source. Integrity remained valid. No real provider/Discord
request was made; supplied data was never sent to a model.

**A simple code rollback is unsafe.** On an isolated copy using the unchanged main
repository implementation, a write reduced 31 unprocessed sources to the old 12-row
limit. A further restoration to a new file matched the original backup signature.
There is no DDL to reverse, but old pruning can still destroy newly retained data.

For deployment/rollback, stop all writers and take/verify a fresh full SQLite backup
plus the deployment's pending-visual sidecar backup where applicable. Prefer a
forward fix retaining pending protection. If reverting code is necessary, keep
writers stopped until a copy rehearsal proves the chosen retention-safe patch or
explicit restore procedure. Restoring an older backup loses later writes and must
not be described as lossless. Do not mix unrelated `-wal`/`-shm` files with a restored
database. Do not deploy an old writer against expanded data simply because its
schema validator accepts it. This PR's merge is not an operational deployment.

Regression tests cover >12 pending inputs, independent ID spaces, source pruning
rollback, restart/deduplication, disabled modes, bounded cleanup and summaries,
provider failure/recovery, explicit deletion and full backup restoration. The normal
CI matrix remains Python 3.11/3.12/3.13 plus Dashboard browser checks.

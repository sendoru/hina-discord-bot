# PDF encoder performance P0–P3

Related: #607, #609, #610, #611, #612.

## Offline reproduction

From an installed checkout, with the same Python/dependencies and fonts on each
revision, run:

```sh
uv run --frozen python scripts/pdf_performance_benchmark.py --runs 7 --rows 120
uv run --frozen python scripts/pdf_performance_benchmark.py --runs 7 --rows 120 --profile
uv run --frozen python scripts/pdf_performance_benchmark.py --runs 7 --rows 120 --memory
```

For the pre-change revision, copy only the benchmark script into a separate
worktree at the baseline commit (9b1a921) or run it from that worktree with the
baseline package installed. The script uses only synthetic records and the
production planner/encoder. It makes no network/provider calls. The lock wait
instrumentation delegates to the original lock and does not change scheduling
policy. Profiling and allocation measurements are separate from ordinary timings.
Profile cumulative times overlap and must not be summed. A cleared font cache is
not a fresh interpreter or a cold filesystem; restart the process for full cold
measurements.

## Preliminary isolated-environment measurements

The available Linux environment had Python 3.13.5, ReportLab 4.4.9 and pypdf
5.9.0 (the repository lock specifies ReportLab 4.5.1 and pypdf 6.21.4). GitHub
clone was unavailable in that environment: the encoder and relevant planner
logic were reconstructed from main and the proposed changes for an isolated
test, with minimal imports stubbed. These are preliminary measurements, not a
full checkout/locked-environment or production baseline. Re-run the checked-in
benchmark on both revisions before comparing against real traces. All inputs
were synthetic; no API calls were made.

Seven warm repetitions after one font-cache-cleared run; times are milliseconds.
The mixed and large partial cases include an unrepresentable codepoint.

| Synthetic workload | Characters | Baseline warm p50 | P0–P3 warm p50 | Baseline/P0–P3 p95 | Attempts before/after | PDF coverage before/after |
|---|---:|---:|---:|---:|---:|---:|
| ASCII candidate records | 15,746 | 36.26 | 28.15 | 37.61 / 32.87 | 1 / 1 | 100% / 100% |
| Mixed candidate records | 15,676 | 98.09 | 76.22 | 166.36 / 78.72 | 15 / 14 | 99.52% / 99.52% |
| Large partial (multiline) | 44,503 | 185.27 | 149.38 | 191.30 / 155.74 | 5 / 5 | 99.99% / 99.99% |
| Long token | 300 | 0.36 | 0.16 | 0.43 / 0.25 | 2 / 1 | 0% / 0% |
| Empty line | 13 | 0.35 | 0.18 | 0.56 / 0.31 | 2 / 1 | 0% / 0% |
| Unicode spaces | 7 | 0.26 | 7.38 | 0.38 / 81.19 | 2 / 1 | 0% / 100% |

Process CPU p50 for the first three cases was 36.26 / 28.14, 97.97 / 76.09,
and 185.24 / 149.32 ms respectively. The Unicode-space case intentionally
does more work because representable content now reaches the PDF.

The approximately 54k-character ASCII encoder-only profile spent 154 ms of
309 ms profiled cumulative time in pypdf page extraction, and 57 ms in
ReportLab textOut. The per-glyph width change alone reduced unprofiled warm
median from 90.2 to 77.1 ms; the encoded PDF bytes were identical. Extraction
validation is retained. For three simultaneous 400-record ASCII requests,
individual completion times were 105 / 202 / 312 ms before and 89 / 178 / 270
ms after; all three retained 100% coverage. This is queueing evidence, not a
claim that ReportLab can safely render concurrently.

The isolated environment lacked coverage for U+53C2 (参). A mixed Korean and
Japanese fixture therefore fell back entirely; the IPA Gothic fallback is
installed in the image and CI for an actual extraction test. The font was not
available locally, so those Japanese-coverage improvements are not included
in the measured results.

## Changes and safeguards

- P1: cache raw glyph widths within each render, avoiding repeated font lists
  and width lookups without changing arithmetic order or line boundaries.
- P2: reuse a single request's bounded paragraph layout, at most 240k
  cached source characters. Rebuild font runs and render and validate the final
  combined document. Reuse the initial deterministic split failure for a
  single original part instead of rendering it twice. No global document or
  private text cache; no new persistence.
- P3: allow space separators only if the installed font and exact extraction
  support them. Control/format characters and unrepresentable glyphs stay
  native; NBSP may still fail roundtrip and must not be normalized.
  Add optional IPA Gothic (installed in Docker and tested in CI).
- P3: reserve split attempts for pending siblings and later sections without
  increasing the configured-in-code attempt limits. In a 1,000-candidate
  synthetic case with only the first record unsupported, native records fell
  from 939 to 7 while total candidates and IDs were retained. The limit can
  still force native fallback for some neighboring records. No changes to
  authorization or reference classification.

The final document still passes extraction verification. The planner retains
verified standalone artifacts for the existing deadline/failed-consolidation
fallback. Static app prompt cross-request caching, parallel rendering,
font shaping changes, and relaxed roundtrip rules are not included.

## Verification still required

- Full repository CI and the benchmark with the locked dependencies and the
  actual production font set. The isolated regression tests passed 13 cases;
  the additional IPA font test needs the installed package.
- Ordinary operational traces for fallback codes, encode attempts, coverage,
  latency and usage. Compare like model/thinking/API/transport settings.
- Actual Gemini input token cost and model comprehension cannot be inferred
  from page count, local PDF bytes or the benchmark. No synthetic paid calls.
- Delegated code execution and density/billing work remain #614 and #615;
  lock/concurrency policy remains #613.

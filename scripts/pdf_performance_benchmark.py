"""Offline production PDF encoder/planner benchmark. No provider or private data.

Run in an installed checkout:
    uv run python scripts/pdf_performance_benchmark.py --runs 7 --rows 120 --profile
Use --memory for a separate allocation measurement (not included in timings).
"""
import argparse
import asyncio
import cProfile
import io
import json
import math
import os
import platform
import pstats
import statistics
import sys
import tracemalloc
from importlib.metadata import version
from pathlib import Path
from time import perf_counter, process_time, thread_time
from unittest.mock import patch

from hina_bot.ai import pdf_transport as pdf
from hina_bot.ai.reference_context_blocks import PDFReferenceText


def cases(rows):
    def records(sample):
        return {"candidates": {
            f"c{i}": {"id": str(10**18 + i), "content": sample}
            for i in range(rows)
        }}

    def candidate(data):
        return pdf.DecisionCandidateReference(
            type="input_text", text=json.dumps(data, ensure_ascii=False, separators=(",", ":")),
        )

    def text(value):
        return PDFReferenceText(type="input_text", text=value)

    ascii_sample = "An ordinary synthetic record with identifiers and a link https://example.invalid/a."
    cjk_sample = "히나가 대화를 읽습니다. 日本語の参考情報。alpha 42"
    yield "ascii", [candidate(records(ascii_sample))]
    yield "cjk", [candidate(records(cjk_sample))]
    yield "emoji_math", [candidate(records(cjk_sample + " 😂 𝒽"))]
    yield "partial", [
        candidate(records(ascii_sample)),
        text("unsupported \U0010ffff"),
        candidate(records(cjk_sample)),
    ]
    mixed = records(ascii_sample)
    mixed["candidates"][f"c{rows // 2}"]["content"] = "unsupported \U0010ffff"
    yield "mixed_records", [candidate(mixed)]
    yield "long_token", [text("a" * 300)]
    yield "spaces", [text("A\u2003B\u202fC\u3000D")]
    yield "empty_line", [text("before\n\nafter")]


def summary(values):
    ordered = sorted(values)
    return {
        "p50": round(statistics.median(ordered), 3),
        "p95": round(ordered[math.ceil(len(ordered) * 0.95) - 1], 3),
    }


class TimedLock:
    """Record wait intervals in the benchmark without changing the encoder."""

    def __init__(self, lock):
        self.lock = lock
        self.waiting = {}
        self.acquired_ms = []

    def acquire(self, *, blocking):
        task = asyncio.current_task()
        started = self.waiting.setdefault(task, perf_counter())
        acquired = self.lock.acquire(blocking=blocking)
        if acquired:
            self.acquired_ms.append((perf_counter() - started) * 1000)
            del self.waiting[task]
        return acquired

    def release(self):
        self.lock.release()


async def measure(parts, *, profile=False, memory=False, concurrency=1):
    real_encode = pdf._encode
    renders = []
    profiles = []
    timed_lock = TimedLock(pdf._LOCK)

    def render(source, **kwargs):
        start = perf_counter()
        cpu = thread_time()
        profiler = cProfile.Profile() if profile else None
        try:
            if profiler is not None:
                return profiler.runcall(real_encode, source, **kwargs)
            return real_encode(source, **kwargs)
        finally:
            renders.append(((perf_counter() - start) * 1000, (thread_time() - cpu) * 1000))
            if profiler is not None:
                profiles.append(profiler)

    async def request():
        start = perf_counter()
        prepared = await pdf.consolidate_references(parts)
        observations = [obs for item in prepared for _, _, obs in item.segments]
        return {
            "wall_ms": (perf_counter() - start) * 1000,
            "attempts": observations[0].get("encode_attempts", 0),
            "initial_fallback": observations[0].get("initial_fallback", ""),
            "consolidation_fallback": observations[0].get("consolidation_fallback", ""),
            "fallbacks": sorted({o["fallback"] for o in observations if o["fallback"]}),
            **pdf.transport_coverage(observations),
        }

    if memory:
        tracemalloc.start()
    start = perf_counter()
    cpu = process_time()
    try:
        with patch.object(pdf, "_encode", render), patch.object(pdf, "_LOCK", timed_lock):
            requests = await asyncio.gather(*(request() for _ in range(concurrency)))
        result = {
            "batch_wall_ms": (perf_counter() - start) * 1000,
            "process_cpu_ms": (process_time() - cpu) * 1000,
            "render_wall_ms": sum(w for w, _ in renders),
            "render_cpu_ms": sum(c for _, c in renders),
            "renders": len(renders),
            "lock_wait_ms": sum(timed_lock.acquired_ms),
            "lock_wait_intervals_ms": timed_lock.acquired_ms,
            "lock_wait_unfinished": len(timed_lock.waiting),
            "requests": requests,
        }
        if memory:
            result["python_peak_bytes"] = tracemalloc.get_traced_memory()[1]
    finally:
        if memory:
            tracemalloc.stop()
    if profiles:
        output = io.StringIO()
        stats = pstats.Stats(profiles[0], stream=output)
        for profiler in profiles[1:]:
            stats.add(profiler)
        stats.sort_stats("cumulative").print_stats(30)
        print(output.getvalue(), file=sys.stderr)
    return result


async def main(args):
    print(json.dumps({
        "python": platform.python_version(), "platform": platform.platform(),
        "reportlab": version("reportlab"), "pypdf": version("pypdf"),
        "font_override": os.getenv("GEMINI_PDF_FONT"), "rows": args.rows,
        "fallback_paths_present": [path for path in pdf._FALLBACK_FONT_PATHS if Path(path).is_file()],
        "runs": args.runs, "concurrency": args.concurrency,
        "billing": None, "model_fidelity": None,
        "cold_definition": "font caches cleared; fresh process needed for full cold start",
    }))
    for name, parts in cases(args.rows):
        if args.case and name not in args.case:
            continue
        pdf._font.cache_clear()
        pdf._fallback_font.cache_clear()
        cold = await measure(parts)
        warm = [await measure(parts) for _ in range(args.runs)]
        result = {
            "case": name, "source_chars": sum(len(pdf.document_source(p)) for p in parts),
            "cold": cold,
            "warm": {key: summary([item[key] for item in warm]) for key in (
                "batch_wall_ms", "process_cpu_ms", "render_wall_ms", "render_cpu_ms", "renders",
                "lock_wait_ms",
            )},
            "warm_requests": warm[-1]["requests"],
        }
        if args.concurrency > 1:
            result["concurrent"] = await measure(parts, concurrency=args.concurrency)
        if args.memory:
            result["memory"] = await measure(parts, memory=True)
        if args.profile:
            print(f"=== {name} ===", file=sys.stderr)
            await measure(parts, profile=True)
        print(json.dumps(result))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runs", type=int, default=7)
    parser.add_argument("--rows", type=int, default=120)
    parser.add_argument("--concurrency", type=int, default=3)
    parser.add_argument("--case", action="append")
    parser.add_argument("--profile", action="store_true")
    parser.add_argument("--memory", action="store_true")
    args = parser.parse_args()
    if min(args.runs, args.rows, args.concurrency) < 1:
        parser.error("runs, rows and concurrency must be positive")
    asyncio.run(main(args))

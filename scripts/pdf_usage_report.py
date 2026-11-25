"""Report input cost estimates for existing Gemini traffic. No provider connection."""
import argparse
import json
import math
from collections import defaultdict


def nonnegative_rate(value):
    rate = float(value)
    if not math.isfinite(rate) or rate < 0:
        raise argparse.ArgumentTypeError("expected a nonnegative finite rate")
    return rate


def summarize(lines, model, api, input_rate, cached_rate):
    groups = defaultdict(lambda: {"observations": 0, "priced": 0, "input_estimate": None})
    for line in lines:
        row = json.loads(line)
        if (row.get("provider"), row.get("model"), row.get("gemini_api")) != ("gemini", model, api):
            continue
        transport = row.get("context_transport")
        if transport not in {"text", "pdf"}:
            continue
        group = groups[(row.get("operation"), transport)]
        group["observations"] += 1
        tokens, cached = row.get("input_tokens"), row.get("cached_tokens")
        if (type(tokens) is not int or type(cached) is not int or not 0 <= cached <= tokens
                or row.get("api_attempts", 1) != 1 or row.get("status") == "error"):
            continue
        cost = ((tokens - cached) * input_rate + cached * cached_rate) / 1_000_000
        group["input_estimate"] = (group["input_estimate"] or 0) + cost
        group["priced"] += 1
    return [{"operation": op, "transport": mode, **metrics} for (op, mode), metrics in groups.items()]


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("usage_log", type=argparse.FileType("r", encoding="utf-8"))
    parser.add_argument("--model", required=True)
    parser.add_argument("--api", choices=("interactions", "generate_content", "decisions"), required=True)
    parser.add_argument("--input-per-million", type=nonnegative_rate, required=True)
    parser.add_argument("--cached-per-million", type=nonnegative_rate, required=True)
    args = parser.parse_args()
    print(json.dumps(summarize(args.usage_log, args.model, args.api,
                               args.input_per_million, args.cached_per_million)))

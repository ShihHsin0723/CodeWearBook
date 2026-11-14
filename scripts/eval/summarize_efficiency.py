"""Summarize measured query latency, LLM usage, and priced LLM calls.

Usage: python scripts/eval/summarize_efficiency.py results_a.jsonl results_b.jsonl
Optional prices JSON maps model ID to USD per million input/output tokens.
"""

import argparse
import json
import math
from pathlib import Path
from statistics import mean, median


DEFAULT_PRICES = Path(__file__).with_name("model_prices.json")


def percentile(values, percent):
    if not values:
        return None
    ordered = sorted(values)
    position = (len(ordered) - 1) * percent / 100
    lower = math.floor(position)
    upper = math.ceil(position)
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)


def cost_for_call(call, prices):
    price = prices.get(call.get("model"))
    if not price or call.get("input_tokens") is None or call.get("output_tokens") is None:
        return None
    if "tiers" in price:
        price = price["tiers"]["over_200k" if call["input_tokens"] > 200_000 else "up_to_200k"]
    cache_write = call.get("cache_creation_input_tokens") or 0
    cache_write_1h = call.get("cache_creation_1h_input_tokens") or 0
    cache_read = call.get("cache_read_input_tokens") or 0
    cached_input = call.get("cached_input_tokens") or 0
    if (cache_write or cache_read) and not all(
        key in price for key in ("cache_write_5m", "cache_write_1h", "cache_read")
    ):
        return None
    if cached_input and "cached_input" not in price:
        return None
    return (
        (call["input_tokens"] - cached_input) * price["input"]
        + cached_input * price.get("cached_input", 0)
        + call["output_tokens"] * price["output"]
        + (cache_write - cache_write_1h) * price.get("cache_write_5m", 0)
        + cache_write_1h * price.get("cache_write_1h", 0)
        + cache_read * price.get("cache_read", 0)
    ) / 1_000_000


def summarize_rows(rows, prices):
    measured = [row for row in rows if row.get("query_elapsed_seconds") is not None]
    latencies = [row["query_elapsed_seconds"] for row in measured]
    llm_latencies = [row["llm_elapsed_seconds"] for row in measured]
    calls = [call for row in measured for call in row.get("llm_calls", [])]
    usage_complete = all(row.get("llm_usage_complete") is True for row in measured)
    total_input_tokens = sum(
        (call.get("input_tokens") or 0)
        + (call.get("cache_creation_input_tokens") or 0)
        + (call.get("cache_read_input_tokens") or 0)
        for call in calls
    )
    total_output_tokens = sum(call.get("output_tokens") or 0 for call in calls)
    costs = [cost_for_call(call, prices) for call in calls]
    models = sorted({call.get("model") for call in calls if call.get("model")})
    providers = sorted({call.get("provider") for call in calls if call.get("provider")})
    sql_rows = [row for row in measured if "sql_match" in row]
    df_rows = [row for row in measured if "dataframe_match" in row]
    return {
        "cases": len(rows),
        "measured_cases": len(measured),
        "providers": providers,
        "models": models,
        "errors": sum(bool(row.get("error") or row.get("generated_error")) for row in measured),
        "sql_match_rate": sum(bool(row["sql_match"]) for row in sql_rows) / len(sql_rows) if sql_rows else None,
        "dataframe_match_rate": sum(bool(row["dataframe_match"]) for row in df_rows) / len(df_rows) if df_rows else None,
        "query_latency_p50_seconds": median(latencies) if latencies else None,
        "query_latency_p95_seconds": percentile(latencies, 95),
        "query_latency_mean_seconds": mean(latencies) if latencies else None,
        "llm_latency_p50_seconds": median(llm_latencies) if llm_latencies else None,
        "llm_latency_p95_seconds": percentile(llm_latencies, 95),
        "llm_latency_mean_seconds": mean(llm_latencies) if llm_latencies else None,
        "llm_calls": len(calls),
        "llm_attempts": sum(call.get("attempts", 0) for call in calls),
        "input_tokens": sum(call.get("input_tokens") or 0 for call in calls),
        "output_tokens": sum(call.get("output_tokens") or 0 for call in calls),
        "total_input_tokens": total_input_tokens,
        "total_tokens": total_input_tokens + total_output_tokens,
        "input_tokens_mean_per_case": total_input_tokens / len(measured) if measured and usage_complete else None,
        "output_tokens_mean_per_case": total_output_tokens / len(measured) if measured and usage_complete else None,
        "total_tokens_mean_per_case": (total_input_tokens + total_output_tokens) / len(measured) if measured and usage_complete else None,
        "cache_creation_input_tokens": sum(call.get("cache_creation_input_tokens") or 0 for call in calls),
        "cache_read_input_tokens": sum(call.get("cache_read_input_tokens") or 0 for call in calls),
        "cached_input_tokens": sum(call.get("cached_input_tokens") or 0 for call in calls),
        "usage_complete": usage_complete,
        "priced_calls": sum(cost is not None for cost in costs),
        "estimated_llm_cost_usd": sum(costs) if measured and all(cost is not None for cost in costs) else None,
        "estimated_llm_cost_per_case_usd": sum(costs) / len(measured) if measured and all(cost is not None for cost in costs) else None,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("results", nargs="+", type=Path)
    parser.add_argument("--prices", type=Path, default=DEFAULT_PRICES)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    prices = json.loads(args.prices.read_text(encoding="utf-8"))["models"]
    summary = {}
    for path in args.results:
        rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
        summary[str(path)] = summarize_rows(rows, prices)
    output = json.dumps(summary, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(output + "\n", encoding="utf-8")
    print(output)


if __name__ == "__main__":
    main()

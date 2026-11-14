#!/usr/bin/env python3
"""Calculate query-level uncertainty from saved results, without model calls."""

import argparse
import hashlib
import json
import math
from pathlib import Path
import random
from statistics import NormalDist, fmean

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def percentile(values, probability):
    ordered = sorted(values)
    position = (len(ordered) - 1) * probability
    lower = math.floor(position)
    upper = math.ceil(position)
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)


def bootstrap_mean_interval(scores, resamples=10000, seed=42):
    if not scores or resamples < 2:
        raise ValueError("Require at least one score and two bootstrap resamples")
    rng = random.Random(seed)
    means = [fmean(rng.choices(scores, k=len(scores))) for _ in range(resamples)]
    return [percentile(means, 0.025), percentile(means, 0.975)]


def wilson_interval(matches, count):
    z = NormalDist().inv_cdf(0.975)
    proportion = matches / count
    denominator = 1 + z * z / count
    center = (proportion + z * z / (2 * count)) / denominator
    half_width = z * math.sqrt(
        proportion * (1 - proportion) / count + z * z / (4 * count * count)
    ) / denominator
    return [max(0.0, center - half_width), min(1.0, center + half_width)]


def load_validated_results(path):
    raw = path.read_bytes()
    rows = [json.loads(line) for line in raw.decode().splitlines() if line.strip()]
    if not rows:
        raise ValueError(f"Empty results: {path}")
    seen = set()
    scores = []
    for number, row in enumerate(rows, 1):
        question = row.get("question")
        if not isinstance(question, str) or not question or question in seen:
            raise ValueError(f"Missing or duplicate question in row {number}: {path}")
        seen.add(question)
        score = row.get("df_match_score")
        if (isinstance(score, bool) or not isinstance(score, (int, float))
                or not math.isfinite(score) or not 0 <= score <= 1):
            raise ValueError(f"Invalid/missing similarity score in row {number}: {path}")
        if not isinstance(row.get("dataframe_match"), bool):
            raise ValueError(f"Invalid/missing exact-match outcome in row {number}: {path}")
        scores.append(score)
    return raw, rows


def summarize(path, resamples=10000, seed=42):
    raw, rows = load_validated_results(path)
    scores = [row["df_match_score"] for row in rows]
    matches = sum(row["dataframe_match"] for row in rows)
    return {
        "source": str(path.resolve()),
        "source_sha256": hashlib.sha256(raw).hexdigest(),
        "questions": len(rows),
        "errors": sum(bool(row.get("error")) for row in rows),
        "exact_matches": matches,
        "exact_match_rate": matches / len(rows),
        "exact_match_wilson_95_ci": wilson_interval(matches, len(rows)),
        "mean_dataframe_similarity": fmean(scores),
        "mean_similarity_bootstrap_95_ci": bootstrap_mean_interval(scores, resamples, seed),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, action="append",
                        help="Saved JSONL results; repeat for multiple files")
    parser.add_argument("--resamples", type=int, default=10000)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--output", type=Path, default=PROJECT_ROOT / "scripts/eval/results/uncertainty/primary_uncertainty.json")
    args = parser.parse_args()
    inputs = args.input or [
        PROJECT_ROOT / f"scripts/eval/results/ablation/{dataset}/{dataset}_full_system.jsonl"
        for dataset in ("bump", "sinc")
    ]
    report = {
        "confidence_level": 0.95,
        "bootstrap_resamples": args.resamples,
        "random_seed": args.seed,
        "resampling_unit": "question, independently within each input file",
        "method": "Percentile bootstrap of the mean; linear percentile interpolation",
        "scope": "Conditional on saved model outputs; does not measure run-to-run model variability or external generalization",
        "results": [summarize(path, args.resamples, args.seed) for path in inputs],
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n")
    for result in report["results"]:
        lo, hi = result["mean_similarity_bootstrap_95_ci"]
        print(f"{Path(result['source']).stem}: n={result['questions']}; "
              f"mean similarity={result['mean_dataframe_similarity']:.4f} "
              f"(95% CI {lo:.4f}–{hi:.4f})")
    print(f"Saved: {args.output}")


if __name__ == "__main__":
    main()

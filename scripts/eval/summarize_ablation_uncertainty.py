#!/usr/bin/env python3
"""Paired question-level bootstrap comparisons from saved ablation results."""

import argparse
import hashlib
import json
from pathlib import Path
from statistics import fmean

from summarize_uncertainty import PROJECT_ROOT, bootstrap_mean_interval, load_validated_results


def compare(full_path, ablation_path, resamples=10000, seed=42):
    full_raw, full_rows = load_validated_results(full_path)
    ablation_raw, ablation_rows = load_validated_results(ablation_path)
    full = {row["question"]: row for row in full_rows}
    ablation = {row["question"]: row for row in ablation_rows}
    if full.keys() != ablation.keys():
        raise ValueError("Full-system and ablation question sets differ; cannot pair all questions")
    questions = sorted(full)
    if any(full[q].get("expected_sql") != ablation[q].get("expected_sql") for q in questions):
        raise ValueError("Reference SQL differs between paired results")
    result = {
        "full_system_source": str(full_path.resolve()),
        "ablation_source": str(ablation_path.resolve()),
        "source_sha256": {"full_system": hashlib.sha256(full_raw).hexdigest(),
                          "ablation": hashlib.sha256(ablation_raw).hexdigest()},
        "paired_questions": len(questions),
        "errors": {"full_system": sum(bool(r.get("error")) for r in full_rows),
                   "ablation": sum(bool(r.get("error")) for r in ablation_rows)},
    }
    for name, key in [("dataframe_similarity", "df_match_score"),
                      ("exact_match_rate", "dataframe_match")]:
        differences = [float(full[q][key]) - float(ablation[q][key]) for q in questions]
        result[name] = {
            "full_system_mean": fmean(float(full[q][key]) for q in questions),
            "ablation_mean": fmean(float(ablation[q][key]) for q in questions),
            "difference_full_minus_ablation": fmean(differences),
            "paired_bootstrap_95_ci": bootstrap_mean_interval(differences, resamples, seed),
        }
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--results-root", type=Path, default=PROJECT_ROOT / "scripts/eval/results/ablation")
    parser.add_argument("--resamples", type=int, default=10000)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--output", type=Path, default=PROJECT_ROOT / "scripts/eval/results/uncertainty/ablation_uncertainty.json")
    args = parser.parse_args()
    comparisons = []
    for dataset in ("bump", "sinc"):
        directory = args.results_root / dataset
        full = directory / f"{dataset}_full_system.jsonl"
        paths = sorted(directory.glob(f"{dataset}_no_*.jsonl"))
        if not paths:
            raise ValueError(f"No ablation results found: {directory}")
        for path in paths:
            comparison = compare(full, path, args.resamples, args.seed)
            comparison.update(dataset=dataset, ablation=path.stem.removeprefix(f"{dataset}_"))
            comparisons.append(comparison)
    report = {
        "confidence_level": 0.95,
        "bootstrap_resamples": args.resamples,
        "random_seed": args.seed,
        "method": "Percentile bootstrap of paired per-question differences; linear percentile interpolation",
        "direction": "Full system minus ablation; positive values favor full system",
        "pairing": "Exact question text; identical question sets and reference SQL required",
        "scope": "Conditional on saved outputs and independent-question assumption; intervals are unadjusted for multiple comparisons",
        "comparisons": comparisons,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n")
    for row in comparisons:
        metric = row["dataframe_similarity"]
        lo, hi = metric["paired_bootstrap_95_ci"]
        print(f"{row['dataset']} {row['ablation']}: "
              f"difference={metric['difference_full_minus_ablation']:.4f} "
              f"(95% CI {lo:.4f}–{hi:.4f})")
    print(f"Saved: {args.output}")


if __name__ == "__main__":
    main()

"""Summarize repeated adverse-event derived-column evaluations."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from statistics import mean, stdev
from typing import Any, Dict, List

import pandas as pd


PROJECT_ROOT = Path(__file__).resolve().parents[2]
ANALYSIS_DIR = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
from clinical_notes_analysis.scripts.evaluate_adverse_event_column_tests import (
    DEFAULT_GOLD_DIR,
    DEFAULT_PROMPTS,
    evaluate_one,
    load_prompt_rows,
)


DEFAULT_RUNS_DIR = ANALYSIS_DIR / "results" / "repeated_runs"


METRIC_COLUMNS = [
    "accuracy_on_matched_rows",
    "correct_labels",
    "incorrect_labels",
    "false_positives",
    "false_negatives",
    "missing_generated_key_rows",
    "ignored_generated_rows_not_in_gold",
    "generated_duplicate_rows",
]


def latest_batch_dir(runs_dir: Path) -> Path:
    batches = sorted([path for path in runs_dir.iterdir() if path.is_dir()])
    if not batches:
        raise FileNotFoundError(f"No repeated-run batch directories found under {runs_dir}")
    return batches[-1]


def evaluate_run(
    run_dir: Path,
    prompt_rows: List[Dict[str, Any]],
    gold_dir: Path,
) -> List[Dict[str, Any]]:
    generated_dir = run_dir / "generated_dfs"
    rows = []
    for prompt_row in prompt_rows:
        result = evaluate_one(prompt_row, gold_dir, generated_dir)
        result["run"] = run_dir.name
        result["generated_dir"] = Path(os.path.relpath(generated_dir, ANALYSIS_DIR)).as_posix()
        rows.append(result)
    return rows


def summarize_results(evaluation_rows: List[Dict[str, Any]]) -> pd.DataFrame:
    clean_rows = [row for row in evaluation_rows if "error" not in row]
    if not clean_rows:
        return pd.DataFrame()

    df = pd.DataFrame(clean_rows)
    summary_rows = []
    for prompt_id, group in df.groupby("id", sort=True):
        summary: Dict[str, Any] = {
            "id": prompt_id,
            "target_column": group["target_column"].iloc[0],
            "runs": len(group),
        }
        for metric in METRIC_COLUMNS:
            values = [float(value) for value in group[metric].tolist()]
            summary[f"{metric}_mean"] = mean(values)
            summary[f"{metric}_std"] = stdev(values) if len(values) > 1 else 0.0
            summary[f"{metric}_min"] = min(values)
            summary[f"{metric}_max"] = max(values)
        summary_rows.append(summary)
    return pd.DataFrame(summary_rows)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Summarize repeated adverse-event column test runs.")
    parser.add_argument("--batch-dir", type=Path, help="Specific repeated-run batch directory.")
    parser.add_argument("--runs-dir", type=Path, default=DEFAULT_RUNS_DIR)
    parser.add_argument("--prompts", type=Path, default=DEFAULT_PROMPTS)
    parser.add_argument("--gold-dir", type=Path, default=DEFAULT_GOLD_DIR)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    batch_dir = args.batch_dir or latest_batch_dir(args.runs_dir)
    run_dirs = sorted([path for path in batch_dir.iterdir() if path.is_dir() and path.name.startswith("run_")])
    if not run_dirs:
        raise FileNotFoundError(f"No run_XX directories found under {batch_dir}")

    prompt_rows = load_prompt_rows(args.prompts)
    evaluation_rows: List[Dict[str, Any]] = []
    for run_dir in run_dirs:
        evaluation_rows.extend(evaluate_run(run_dir, prompt_rows, args.gold_dir))

    details_path = batch_dir / "repeated_evaluation_details.json"
    summary_json_path = batch_dir / "repeated_evaluation_summary.json"
    summary_csv_path = batch_dir / "repeated_evaluation_summary.csv"

    details_path.write_text(json.dumps(evaluation_rows, indent=2, ensure_ascii=False), encoding="utf-8")
    summary_df = summarize_results(evaluation_rows)
    summary_df.to_json(summary_json_path, orient="records", indent=2, force_ascii=False)
    summary_df.to_csv(summary_csv_path, index=False)

    display_columns = [
        "id",
        "runs",
        "accuracy_on_matched_rows_mean",
        "accuracy_on_matched_rows_std",
        "incorrect_labels_mean",
        "incorrect_labels_std",
        "false_positives_mean",
        "false_negatives_mean",
    ]
    if not summary_df.empty:
        print(summary_df.loc[:, display_columns].to_string(index=False))
    else:
        print("No successful evaluation rows found.")

    errors = [row for row in evaluation_rows if "error" in row]
    if errors:
        print(f"\nEvaluation errors: {len(errors)}")
        for row in errors[:10]:
            print(f"{row.get('run')} {row.get('id')}: {row.get('error')}")

    print(f"\nDetails written to: {details_path}")
    print(f"Summary JSON written to: {summary_json_path}")
    print(f"Summary CSV written to: {summary_csv_path}")


if __name__ == "__main__":
    main()

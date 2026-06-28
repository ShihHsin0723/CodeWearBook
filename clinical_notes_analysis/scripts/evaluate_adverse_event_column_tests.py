"""Evaluate generated adverse-event derived columns against saved gold CSVs."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Dict, List

import pandas as pd


ANALYSIS_DIR = Path(__file__).resolve().parents[1]
DEFAULT_PROMPTS = ANALYSIS_DIR / "data" / "prompts" / "adverse_event_column_prompts.jsonl"
DEFAULT_GOLD_DIR = ANALYSIS_DIR / "data" / "gold"
DEFAULT_GENERATED_DIR = ANALYSIS_DIR / "results" / "generated_dfs"
DEFAULT_OUTPUT = ANALYSIS_DIR / "results" / "adverse_event_column_accuracy.json"
KEY_COLUMNS = ["record_id", "redcap_event_name", "date", "ae_describe"]


def load_prompt_rows(path: Path) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def normalize_text(value: Any) -> str:
    if pd.isna(value):
        return ""
    return " ".join(str(value).strip().split())


def normalize_label(value: Any) -> str:
    text = normalize_text(value).lower()
    if text in {"yes", "y", "true", "1"}:
        return "Yes"
    if text in {"no", "n", "false", "0", ""}:
        return "No"
    return normalize_text(value)


def normalize_for_compare(df: pd.DataFrame, target_column: str) -> pd.DataFrame:
    output_df = df.copy()
    output_df.columns = [str(column).strip() for column in output_df.columns]

    stripped_target = target_column.strip()
    matching_columns = [column for column in output_df.columns if column.strip().lower() == stripped_target.lower()]
    if matching_columns and matching_columns[0] != target_column:
        output_df = output_df.rename(columns={matching_columns[0]: target_column})

    required_columns = KEY_COLUMNS + [target_column]
    missing = [column for column in required_columns if column not in output_df.columns]
    if missing:
        raise ValueError(f"Missing required generated/gold column(s): {missing}")

    output_df = output_df.loc[:, required_columns].copy()
    for column in KEY_COLUMNS:
        output_df[column] = output_df[column].map(normalize_text)
    output_df[target_column] = output_df[target_column].map(normalize_label)
    return output_df.sort_values(KEY_COLUMNS).reset_index(drop=True)


def evaluate_one(prompt_row: Dict[str, Any], gold_dir: Path, generated_dir: Path) -> Dict[str, Any]:
    prompt_id = prompt_row["id"]
    target_column = prompt_row["target_column"]
    gold_path = gold_dir / f"{prompt_id}.csv"
    generated_path = generated_dir / f"{prompt_id}.csv"

    if not gold_path.exists():
        return {"id": prompt_id, "target_column": target_column, "error": f"Missing gold CSV: {gold_path}"}
    if not generated_path.exists():
        return {"id": prompt_id, "target_column": target_column, "error": f"Missing generated CSV: {generated_path}"}

    gold_df = normalize_for_compare(pd.read_csv(gold_path), target_column)
    generated_df = normalize_for_compare(pd.read_csv(generated_path), target_column)

    generated_key_label_conflicts = int(
        (
            generated_df.groupby(KEY_COLUMNS)[target_column]
            .nunique()
            .reset_index(name="label_count")["label_count"]
            > 1
        ).sum()
    )
    generated_duplicate_rows = len(generated_df) - len(generated_df.drop_duplicates(KEY_COLUMNS + [target_column]))
    generated_df = generated_df.drop_duplicates(KEY_COLUMNS + [target_column]).drop_duplicates(KEY_COLUMNS, keep="first")

    gold_keys = gold_df.loc[:, KEY_COLUMNS].drop_duplicates()
    generated_in_gold = generated_df.merge(gold_keys, on=KEY_COLUMNS, how="inner")
    ignored_generated_rows = len(generated_df) - len(generated_in_gold)

    merged = gold_df.merge(
        generated_in_gold,
        on=KEY_COLUMNS,
        how="left",
        suffixes=("_gold", "_generated"),
        indicator=True,
    )
    matched_rows = merged["_merge"].eq("both")
    comparable = merged.loc[matched_rows].copy()
    correct = comparable[f"{target_column}_gold"].eq(comparable[f"{target_column}_generated"])

    false_positive = comparable[f"{target_column}_gold"].eq("No") & comparable[f"{target_column}_generated"].eq("Yes")
    false_negative = comparable[f"{target_column}_gold"].eq("Yes") & comparable[f"{target_column}_generated"].eq("No")

    return {
        "id": prompt_id,
        "target_column": target_column,
        "gold_rows": len(gold_df),
        "generated_rows_raw": len(pd.read_csv(generated_path)),
        "generated_rows_distinct_keys": len(generated_df),
        "generated_duplicate_rows": int(generated_duplicate_rows),
        "generated_key_label_conflicts": generated_key_label_conflicts,
        "generated_rows_compared": len(generated_in_gold),
        "ignored_generated_rows_not_in_gold": int(ignored_generated_rows),
        "matched_key_rows": int(matched_rows.sum()),
        "missing_generated_key_rows": int(merged["_merge"].eq("left_only").sum()),
        "extra_generated_key_rows": int(ignored_generated_rows),
        "correct_labels": int(correct.sum()),
        "incorrect_labels": int((~correct).sum()),
        "accuracy_on_matched_rows": float(correct.mean()) if len(correct) else 0.0,
        "false_positives": int(false_positive.sum()),
        "false_negatives": int(false_negative.sum()),
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Evaluate adverse-event derived-column generated CSVs.")
    parser.add_argument("--prompts", type=Path, default=DEFAULT_PROMPTS)
    parser.add_argument("--gold-dir", type=Path, default=DEFAULT_GOLD_DIR)
    parser.add_argument("--generated-dir", type=Path, default=DEFAULT_GENERATED_DIR)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    results = [evaluate_one(row, args.gold_dir, args.generated_dir) for row in load_prompt_rows(args.prompts)]
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(results, indent=2, ensure_ascii=False), encoding="utf-8")

    for row in results:
        if "error" in row:
            print(f"{row['id']}: {row['error']}")
            continue
        print(
            f"{row['id']}: accuracy={row['accuracy_on_matched_rows']:.3f}, "
            f"incorrect={row['incorrect_labels']}, missing_keys={row['missing_generated_key_rows']}, "
            f"ignored_not_in_gold={row['ignored_generated_rows_not_in_gold']}, "
            f"duplicate_generated_rows={row['generated_duplicate_rows']}"
        )
    print(f"Wrote evaluation results to {args.output}")


if __name__ == "__main__":
    main()

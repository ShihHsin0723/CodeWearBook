"""Create gold CSVs for derived adverse-event columns I through P."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Dict, List

import pandas as pd


ANALYSIS_DIR = Path(__file__).resolve().parents[1]
DEFAULT_SOURCE_CSV = ANALYSIS_DIR / "data" / "source" / "adverse_event_report_dataframe.csv"
DEFAULT_PROMPTS = ANALYSIS_DIR / "data" / "prompts" / "adverse_event_column_prompts.jsonl"
DEFAULT_OUTPUT_DIR = ANALYSIS_DIR / "data" / "gold"
DEFAULT_MODIFICATION_LOG = ANALYSIS_DIR / "data" / "gold" / "modifications.csv"
KEY_COLUMNS = ["record_id", "redcap_event_name", "date", "ae_describe"]
GOLD_LABEL_OVERRIDES = {
    (
        "Covid",
        "direct_252",
        "ci_6_arm_2",
        "2022-12-27",
    ): "Yes",
    (
        "Covid",
        "direct_243",
        "ci_6_arm_2",
        "2022-12-10",
    ): "Yes",
    (
        "ER/ED visit",
        "sema4_508",
        "ci_3_arm_1",
        "2022-05-26",
    ): "Yes",
    (
        "ER/ED visit",
        "direct_481",
        "ci_12_arm_2",
        "2023-03-27",
    ): "No",
    (
        "OB triage or L&D (Labor and Delivery) triage",
        "direct_374",
        "ci_6_arm_2",
        "2022-12-09",
    ): "Yes",
    (
        "OB triage or L&D (Labor and Delivery) triage",
        "direct_481",
        "ci_12_arm_2",
        "2023-03-27",
    ): "Yes",
    (
        "OB triage or L&D (Labor and Delivery) triage",
        "sema4_4",
        "ci_8_arm_1",
        "2021-06-24",
    ): "No",
    (
        "OB triage or L&D (Labor and Delivery) triage",
        "sema4_799",
        "ci_18_arm_1",
        "2023-02-14",
    ): "Yes",
    (
        "OB triage or L&D (Labor and Delivery) triage",
        "sema4_831",
        "ci_19_arm_1",
        "2023-02-16",
    ): "Yes",
    (
        "OB triage or L&D (Labor and Delivery) triage",
        "sema4_880",
        "ci_4_arm_1",
        "2022-09-06",
    ): "Yes",
    (
        "OB triage or L&D (Labor and Delivery) triage",
        "sema4_886",
        "ci_1_arm_1",
        "2022-08-10",
    ): "Yes",
    (
        "whether fell down",
        "sema4_4",
        "ci_8_arm_1",
        "2021-06-24",
    ): "Yes",
    (
        "Hospitalized/ overnight stay or ER/ED or OB triage/L&D",
        "direct_374",
        "ci_6_arm_2",
        "2022-12-09",
    ): "Yes",
    (
        "Hospitalized/ overnight stay or ER/ED or OB triage/L&D",
        "sema4_508",
        "ci_3_arm_1",
        "2022-05-26",
    ): "Yes",
    (
        "Hospitalized/ overnight stay or ER/ED or OB triage/L&D",
        "sema4_799",
        "ci_18_arm_1",
        "2023-02-14",
    ): "Yes",
    (
        "Hospitalized/ overnight stay or ER/ED or OB triage/L&D",
        "sema4_831",
        "ci_19_arm_1",
        "2023-02-16",
    ): "Yes",
    (
        "Hospitalized/ overnight stay or ER/ED or OB triage/L&D",
        "sema4_880",
        "ci_4_arm_1",
        "2022-09-06",
    ): "Yes",
    (
        "Hospitalized/ overnight stay or ER/ED or OB triage/L&D",
        "sema4_886",
        "ci_1_arm_1",
        "2022-08-10",
    ): "Yes",
    (
        "Hospitalized/ overnight stay or ER/ED or OB triage/L&D",
        "sema4_124",
        "ci_22_arm_1",
        "2022-07-21",
    ): "Yes",
    (
        "Hospitalized/ overnight stay or ER/ED or OB triage/L&D",
        "sema4_133",
        "ci_16_arm_1",
        "2022-02-18",
    ): "Yes",
    (
        "Hospitalized/ overnight stay or ER/ED or OB triage/L&D",
        "sema4_615",
        "postnatal_checkin_arm_1",
        "2022-08-18",
    ): "Yes",
}


def load_prompt_rows(path: Path) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as f:
        for line_num, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            row = json.loads(line)
            if not row.get("id") or not row.get("target_column"):
                raise ValueError(f"Missing id or target_column in {path} line {line_num}")
            rows.append(row)
    return rows


def normalize_gold_value(value: Any) -> str:
    if pd.isna(value):
        return "No"
    text = str(value).strip()
    if text.lower() in {"yes", "y", "true", "1"}:
        return "Yes"
    return "No"


def build_gold_dataframe(source_df: pd.DataFrame, target_column: str) -> pd.DataFrame:
    output_columns = KEY_COLUMNS + [target_column]
    missing = [column for column in output_columns if column not in source_df.columns]
    if missing:
        raise ValueError(f"Source dataframe is missing expected column(s): {missing}")

    gold_df = source_df.loc[source_df["ae_describe"].notna(), output_columns].copy()
    gold_df[target_column] = gold_df[target_column].map(normalize_gold_value)
    for (override_column, record_id, redcap_event_name, date), value in GOLD_LABEL_OVERRIDES.items():
        if override_column != target_column:
            continue
        mask = (
            gold_df["record_id"].astype(str).eq(record_id)
            & gold_df["redcap_event_name"].astype(str).eq(redcap_event_name)
            & gold_df["date"].astype(str).eq(date)
        )
        gold_df.loc[mask, target_column] = value
    return gold_df.sort_values(KEY_COLUMNS).reset_index(drop=True)


def build_modification_log(source_df: pd.DataFrame, output_path: Path = DEFAULT_MODIFICATION_LOG) -> None:
    rows = []
    for (target_column, record_id, redcap_event_name, date), updated_gold_value in GOLD_LABEL_OVERRIDES.items():
        mask = (
            source_df["record_id"].astype(str).eq(record_id)
            & source_df["redcap_event_name"].astype(str).eq(redcap_event_name)
            & source_df["date"].astype(str).eq(date)
        )
        matched_rows = source_df.loc[mask]
        if matched_rows.empty:
            original_value = ""
            ae_describe = ""
        else:
            source_row = matched_rows.iloc[0]
            original_value = normalize_gold_value(source_row.get(target_column))
            ae_describe = source_row.get("ae_describe", "")

        rows.append(
            {
                "target_column": target_column,
                "record_id": record_id,
                "redcap_event_name": redcap_event_name,
                "date": date,
                "original_gold_value": original_value,
                "updated_gold_value": updated_gold_value,
                "ae_describe": ae_describe,
            }
        )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows).sort_values(["target_column", "record_id", "redcap_event_name", "date"]).to_csv(
        output_path,
        index=False,
    )
    print(f"Wrote gold modification log to {output_path}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build gold CSVs for adverse event derived-column tests.")
    parser.add_argument("--source-csv", type=Path, default=DEFAULT_SOURCE_CSV)
    parser.add_argument("--prompts", type=Path, default=DEFAULT_PROMPTS)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--modification-log", type=Path, default=DEFAULT_MODIFICATION_LOG)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    source_df = pd.read_csv(args.source_csv)
    prompt_rows = load_prompt_rows(args.prompts)
    args.output_dir.mkdir(parents=True, exist_ok=True)

    for row in prompt_rows:
        gold_df = build_gold_dataframe(source_df, row["target_column"])
        output_path = args.output_dir / f"{row['id']}.csv"
        gold_df.to_csv(output_path, index=False)
        print(f"Wrote {len(gold_df)} rows to {output_path}")
    build_modification_log(source_df, args.modification_log)


if __name__ == "__main__":
    main()

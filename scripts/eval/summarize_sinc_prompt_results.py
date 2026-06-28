import argparse
import json
import sys
from pathlib import Path

from sqlalchemy import create_engine

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from config import DB_CONNECTION_STRING
from dataset_registry import SCOPE_SINC, create_tables_for_scope, load_data_for_scope
from scripts.eval.prompt_eval_utils import (
    load_generated_df_from_csv_or_sql,
    load_rows,
    summarize_prompt_rows,
    to_json_safe,
)


def create_sinc_in_memory_engine():
    engine = create_engine("sqlite:///:memory:", echo=False)
    create_tables_for_scope(engine, SCOPE_SINC)
    load_data_for_scope(engine, SCOPE_SINC)
    return engine


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Summarize SQL/DF matching metrics across SinC prompts."
    )
    parser.add_argument(
        "--input",
        type=Path,
        default=Path("scripts/eval/results/archive/sinc/results.jsonl"),
        help="Mirrored SinC prompt results JSONL.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("scripts/eval/results/archive/sinc/results.summary.json"),
        help="Path to write the aggregate summary JSON.",
    )
    parser.add_argument(
        "--shared-db",
        action="store_true",
        help="Use the configured shared SQLite database instead of rebuilding an in-memory SinC database.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    rows = load_rows(args.input)
    if args.shared_db:
        engine = create_engine(DB_CONNECTION_STRING)
    else:
        engine = create_sinc_in_memory_engine()
    summary = summarize_prompt_rows(rows, engine, load_generated_df_from_csv_or_sql)

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(to_json_safe(summary), indent=2, ensure_ascii=False, allow_nan=False),
        encoding="utf-8",
    )

    console_summary = {k: v for k, v in summary.items() if k != "case_details"}
    print(json.dumps(to_json_safe(console_summary), indent=2, ensure_ascii=False))
    print(f"\nWrote summary to: {args.output}")


if __name__ == "__main__":
    main()

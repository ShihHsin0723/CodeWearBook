import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import query_system as uqs
from scripts.eval.prompt_eval_utils import (
    load_generated_df_from_csv_or_sql,
    load_rows,
    summarize_prompt_rows,
    to_json_safe,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Summarize SQL/dataframe matching metrics across curated BUMP prompts."
    )
    parser.add_argument(
        "--input",
        type=Path,
        default=Path("scripts/eval/results/bump/results.jsonl"),
        help="Mirrored BUMP prompt results JSONL.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("scripts/eval/results/bump/results.summary.json"),
        help="Path to write the aggregate summary JSON.",
    )
    parser.add_argument(
        "--shared-db",
        action="store_true",
        help="Use the shared DB connection instead of the in-memory SQLite database.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    rows = load_rows(args.input)

    if args.shared_db:
        engine = uqs.create_shared_db_engine()
    else:
        engine = uqs.create_in_memory_db_engine()

    summary = summarize_prompt_rows(rows, engine, load_generated_df_from_csv_or_sql)

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        __import__("json").dumps(to_json_safe(summary), indent=2, ensure_ascii=False, allow_nan=False),
        encoding="utf-8",
    )

    console_summary = {k: v for k, v in summary.items() if k != "case_details"}
    print(__import__("json").dumps(to_json_safe(console_summary), indent=2, ensure_ascii=False))
    print(f"\nWrote summary to: {args.output}")


if __name__ == "__main__":
    main()

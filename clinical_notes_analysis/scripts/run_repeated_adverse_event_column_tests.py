"""Run adverse-event derived-column prompts multiple times and save each run separately."""

from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List

import pandas as pd


PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from clinical_notes_analysis.scripts.run_adverse_event_column_tests import (
    DEFAULT_MPLCONFIGDIR,
    DEFAULT_PROMPTS,
    load_prompt_rows,
    patched_input,
    save_output,
    build_run_metadata,
    save_run_metadata,
    select_prompt_rows,
    update_result_row,
)


ANALYSIS_DIR = Path(__file__).resolve().parents[1]
DEFAULT_RUNS_DIR = ANALYSIS_DIR / "results" / "repeated_runs"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run adverse-event derived-column prompts multiple times and save every run."
    )
    parser.add_argument("--prompts", type=Path, default=DEFAULT_PROMPTS)
    parser.add_argument("--runs-dir", type=Path, default=DEFAULT_RUNS_DIR)
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--id", help="Run only one prompt id from adverse_event_column_prompts.jsonl.")
    parser.add_argument("--target-column", help="Run only one target column.")
    parser.add_argument("--in-memory", action="store_true", help="Reload BUMP into an in-memory SQLite database.")
    return parser.parse_args()


def run_one_prompt(
    row: Dict[str, Any],
    engine,
    schema_retriever,
    kb_retriever,
    scope: str,
) -> tuple[pd.DataFrame | None, str, str | None]:
    import query_system as uqs

    result_df = None
    sql_query = ""
    error = None
    print(f"\n=== Running prompt: {row['id']} ===")
    print(row["question"])

    try:
        with patched_input():
            result_df, sql_query, _explanation = uqs.process_natural_query(
                row["question"],
                engine,
                schema_retriever,
                kb_retriever,
                impute="no",
                session_scope=scope,
            )
    except Exception as exc:
        error = str(exc)
        print(f"Prompt failed: {error}")

    return result_df, sql_query, error


def main() -> None:
    args = parse_args()
    if args.repeats < 1:
        raise ValueError("--repeats must be at least 1")

    run_batch_id = datetime.now().strftime("%Y%m%d_%H%M%S")
    batch_dir = args.runs_dir / run_batch_id
    batch_dir.mkdir(parents=True, exist_ok=True)

    prompt_rows = select_prompt_rows(
        load_prompt_rows(args.prompts),
        args.id,
        args.target_column,
        question=None,
    )
    save_run_metadata(
        batch_dir / "run_metadata.json",
        build_run_metadata(prompt_rows, mode="repeated_run", repeats=args.repeats),
    )

    os.environ.setdefault("MPLCONFIGDIR", str(DEFAULT_MPLCONFIGDIR))
    DEFAULT_MPLCONFIGDIR.mkdir(parents=True, exist_ok=True)
    import query_system as uqs

    engine = uqs.create_in_memory_db_engine(uqs.SCOPE_BUMP) if args.in_memory else uqs.create_shared_db_engine()
    retrieval_context = uqs.initialize_retrieval_context(engine, uqs.SCOPE_BUMP)
    schema_retriever = retrieval_context["schema_retriever"]
    kb_retriever = retrieval_context["kb_retriever"]

    for repeat_index in range(1, args.repeats + 1):
        run_dir = batch_dir / f"run_{repeat_index:02d}"
        df_dir = run_dir / "generated_dfs"
        output_path = run_dir / "adverse_event_column_results.jsonl"
        run_rows: List[Dict[str, Any]] = [dict(row) for row in prompt_rows]

        print(f"\n\n##### Repeat {repeat_index}/{args.repeats}: {run_dir} #####")
        for row in run_rows:
            result_df, sql_query, error = run_one_prompt(
                row,
                engine,
                schema_retriever,
                kb_retriever,
                uqs.SCOPE_BUMP,
            )
            update_result_row(row, result_df, sql_query, error, df_dir)
            save_output(output_path, run_rows)

    print(f"\nRepeated run batch written to: {batch_dir}")
    print("Evaluate with:")
    print(
        "venv/bin/python clinical_notes_analysis/scripts/"
        f"summarize_repeated_adverse_event_column_tests.py --batch-dir {batch_dir}"
    )


if __name__ == "__main__":
    main()

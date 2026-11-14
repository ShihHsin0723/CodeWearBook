import argparse
import builtins
import contextlib
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import query_system as uqs
from llm_metrics import QueryMetrics
from scripts.eval.prompt_eval_utils import dataframe_preview, to_json_safe


def load_prompts(path: Path) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as f:
        for line_num, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            item = json.loads(line)
            if "question" not in item or "sql" not in item:
                raise ValueError(
                    f"Missing required keys in {path} line {line_num}. Expected 'question' and 'sql'."
                )
            rows.append(item)
    return rows


def load_existing_output(path: Path) -> Dict[str, Dict[str, Any]]:
    if not path.exists():
        return {}

    rows_by_question: Dict[str, Dict[str, Any]] = {}
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            item = json.loads(line)
            question = item.get("question")
            if isinstance(question, str) and question.strip():
                rows_by_question[question.strip()] = item
    return rows_by_question


def merge_rows(
    prompts: List[Dict[str, Any]],
    existing_output_by_question: Dict[str, Dict[str, Any]],
) -> List[Dict[str, Any]]:
    merged: List[Dict[str, Any]] = []
    for row in prompts:
        question = row["question"].strip()
        merged_row = dict(row)
        existing_row = existing_output_by_question.get(question, {})
        for key in (
            "generated_sql",
            "generated_df_preview",
            "generated_df_csv",
            "generated_at",
            "generated_error",
            "query_elapsed_seconds", "llm_elapsed_seconds", "llm_call_count",
            "llm_input_tokens", "llm_output_tokens", "llm_total_input_tokens",
            "llm_total_tokens", "llm_usage_complete", "llm_calls",
        ):
            if key in existing_row:
                merged_row[key] = existing_row[key]
        merged.append(merged_row)
    return merged


def save_output(path: Path, rows: List[Dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(to_json_safe(row), ensure_ascii=False) + "\n")


def normalize_sql(sql: str) -> str:
    if not sql:
        return ""
    return " ".join(sql.split())


def _auto_input(prompt: str = "") -> str:
    normalized_prompt = prompt.lower()

    if "select one or more options" in normalized_prompt:
        return "1"
    if "describe the data you want" in normalized_prompt:
        return ""
    if "describe the table or fields you expected" in normalized_prompt:
        return ""
    if "[feedback]" in normalized_prompt:
        return ""

    return ""


@contextlib.contextmanager
def patched_input():
    original_input = builtins.input
    builtins.input = _auto_input
    try:
        yield
    finally:
        builtins.input = original_input


def update_match_record(
    matched_row: Dict[str, Any],
    result_df: Optional[pd.DataFrame],
    sql_query: str,
    error: Optional[str],
    df_output_dir: Path,
) -> None:
    matched_row["generated_sql"] = normalize_sql(sql_query)
    matched_row["generated_at"] = datetime.now().isoformat(timespec="seconds")
    matched_row["generated_error"] = error

    if isinstance(result_df, pd.DataFrame):
        safe_name = "".join(ch if ch.isalnum() else "_" for ch in matched_row["question"].lower()).strip("_")
        safe_name = safe_name[:80] or "question"
        csv_path = df_output_dir / f"{safe_name}.csv"
        csv_path.parent.mkdir(parents=True, exist_ok=True)
        result_df.to_csv(csv_path, index=False)
        matched_row["generated_df_csv"] = str(csv_path)
        matched_row["generated_df_preview"] = dataframe_preview(result_df, max_rows=5)
    else:
        matched_row["generated_df_csv"] = None
        matched_row["generated_df_preview"] = {"row_count": None, "preview_rows": []}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run the SinC prompt set through query_system and record SQL/result metadata."
    )
    parser.add_argument(
        "--prompts",
        type=Path,
        default=Path("prompts/curated/sinc_prompts.jsonl"),
        help="Path to the SinC prompts JSONL.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("scripts/eval/results/archive/sinc/results.jsonl"),
        help="Path to write the mirrored prompts JSONL with generated SQL/result metadata.",
    )
    parser.add_argument(
        "--df-dir",
        type=Path,
        default=Path("scripts/eval/results/archive/sinc/dfs"),
        help="Directory where generated dataframes are saved as CSV.",
    )
    parser.add_argument(
        "--shared-db",
        action="store_true",
        help="Use the shared DB connection instead of the in-memory SQLite database.",
    )
    parser.add_argument(
        "--rerun-tested",
        action="store_true",
        help="Re-run prompts even if they already have generated SQL or an error recorded.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    prompts = load_prompts(args.prompts)

    existing_output = load_existing_output(args.output)
    output_rows = merge_rows(prompts, existing_output)

    if args.shared_db:
        engine = uqs.create_shared_db_engine()
    else:
        engine = uqs.create_in_memory_db_engine(uqs.SCOPE_SINC)

    retrieval_context = uqs.initialize_retrieval_context(engine, uqs.SCOPE_SINC)
    schema_retriever = retrieval_context["schema_retriever"]
    kb_retriever = retrieval_context["kb_retriever"]

    completed = 0
    skipped = 0

    for row in output_rows:
        question = row["question"].strip()
        already_tested = bool((row.get("generated_sql") or "").strip() or row.get("generated_error"))
        if already_tested and not args.rerun_tested:
            skipped += 1
            continue

        result_df = None
        sql_query = ""
        error = None
        metrics = QueryMetrics()

        print(f"\n=== Running SinC prompt ===\n{question}\n")
        try:
            with patched_input():
                with metrics:
                    result_df, sql_query, _explanation = uqs.process_natural_query(
                        question, engine, schema_retriever, kb_retriever,
                        impute="no", session_scope=uqs.SCOPE_SINC,
                    )
        except Exception as exc:
            error = str(exc)
            print(f"Prompt failed: {error}")

        update_match_record(
            row,
            result_df,
            sql_query,
            error,
            args.df_dir,
        )
        row.update(metrics.as_dict())
        save_output(args.output, output_rows)
        completed += 1

    print(f"\nCompleted: {completed}")
    print(f"Skipped existing: {skipped}")
    print(f"Results written to: {args.output}")


if __name__ == "__main__":
    main()

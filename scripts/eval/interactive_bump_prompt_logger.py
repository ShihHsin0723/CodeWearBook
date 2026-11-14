import argparse
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


def to_json_safe(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, bool)):
        return value
    if isinstance(value, float):
        if pd.isna(value):
            return None
        return value
    if isinstance(value, (pd.Timestamp, pd.Timedelta)):
        return str(value)
    if isinstance(value, dict):
        return {k: to_json_safe(v) for k, v in value.items()}
    if isinstance(value, list):
        return [to_json_safe(v) for v in value]
    if isinstance(value, tuple):
        return [to_json_safe(v) for v in value]
    if hasattr(value, "item"):
        try:
            return to_json_safe(value.item())
        except Exception:
            pass
    return str(value)


def dataframe_preview(df: Optional[pd.DataFrame], max_rows: int = 5) -> Dict[str, Any]:
    if not isinstance(df, pd.DataFrame):
        return {"row_count": None, "preview_rows": []}
    return {
        "row_count": int(len(df)),
        "preview_rows": to_json_safe(df.head(max_rows).to_dict(orient="records")),
    }


def normalize_sql(sql: str) -> str:
    if not sql:
        return ""
    return " ".join(sql.split())


def initialize_querying_context(engine):
    retrieval_context = uqs.initialize_retrieval_context(engine, uqs.SCOPE_BUMP)
    return retrieval_context["schema_retriever"], retrieval_context["kb_retriever"]


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
        description="Interactive wrapper that records SQL/dataframes for matched curated BUMP prompts."
    )
    parser.add_argument(
        "--prompts",
        type=Path,
        default=Path("prompts/curated/bump_prompts.jsonl"),
        help="Path to the curated BUMP prompts JSONL.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("scripts/eval/results/bump/results.jsonl"),
        help="Path to write the mirrored prompts JSONL with generated SQL/result metadata.",
    )
    parser.add_argument(
        "--df-dir",
        type=Path,
        default=Path("scripts/eval/results/bump/dfs"),
        help="Directory where matched question result dataframes are saved as CSV.",
    )
    parser.add_argument(
        "--shared-db",
        action="store_true",
        help="Use the shared DB connection instead of the in-memory SQLite database.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    prompts = load_prompts(args.prompts)
    prompts_by_question = {row["question"].strip(): row for row in prompts}

    existing_output = load_existing_output(args.output)
    output_rows = merge_rows(prompts, existing_output)
    output_rows_by_question = {row["question"].strip(): row for row in output_rows}
    save_output(args.output, output_rows)

    if args.shared_db:
        engine = uqs.create_shared_db_engine()
    else:
        engine = uqs.create_in_memory_db_engine()

    schema_retriever, kb_retriever = initialize_querying_context(engine)

    print("\nWelcome to the BUMP prompt logger. Type 'exit' to quit.")
    print(f"Loaded {len(prompts_by_question)} curated BUMP prompt question(s).")
    print(f"Matched results will be written to {args.output}")

    while True:
        natural_query = input("\nAsk a question: ").strip()
        if natural_query.lower() == "exit":
            print("Goodbye!")
            break
        if not natural_query:
            continue

        result_df = None
        sql_query = ""
        explanation = ""
        error = None
        metrics = QueryMetrics()

        try:
            with metrics:
                result_df, sql_query, explanation = uqs.process_natural_query(
                    natural_query, engine, schema_retriever, kb_retriever,
                    impute="no",
                )
        except Exception as exc:
            error = str(exc)
            print(f"Query failed: {error}")

        if isinstance(result_df, pd.DataFrame):
            print("Query Results:")
            print(result_df)
            print()
            print("Explanation:")
            print(explanation)
        elif sql_query:
            print("Generated SQL:")
            print(sql_query)
        elif error is None:
            print(result_df)

        matched_row = output_rows_by_question.get(natural_query)
        if matched_row is None:
            print("Question not found in curated BUMP prompts. Nothing was recorded.")
            continue

        update_match_record(
            matched_row,
            result_df,
            sql_query,
            error,
            args.df_dir,
        )
        matched_row.update(metrics.as_dict())
        save_output(args.output, output_rows)
        print(f"Recorded generated SQL/results for matched BUMP prompt in {args.output}")


if __name__ == "__main__":
    main()

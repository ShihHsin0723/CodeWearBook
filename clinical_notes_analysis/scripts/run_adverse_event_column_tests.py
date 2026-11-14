"""Run adverse-event derived-column prompts and save generated dataframes."""

from __future__ import annotations

import argparse
import builtins
import contextlib
import json
import os
import platform
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import pandas as pd


PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

EVAL_SCRIPTS_DIR = PROJECT_ROOT / "scripts" / "eval"
if str(EVAL_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(EVAL_SCRIPTS_DIR))

from prompt_eval_utils import dataframe_preview, to_json_safe
from llm_metrics import QueryMetrics


ANALYSIS_DIR = Path(__file__).resolve().parents[1]
DEFAULT_PROMPTS = ANALYSIS_DIR / "data" / "prompts" / "adverse_event_column_prompts.jsonl"
DEFAULT_OUTPUT = ANALYSIS_DIR / "results" / "adverse_event_column_results.jsonl"
DEFAULT_DF_DIR = ANALYSIS_DIR / "results" / "generated_dfs"
DEFAULT_MPLCONFIGDIR = ANALYSIS_DIR / "results" / "cache" / "matplotlib"
DEFAULT_METADATA_OUTPUT = ANALYSIS_DIR / "results" / "run_metadata.json"


def load_prompt_rows(path: Path) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as f:
        for line_num, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            row = json.loads(line)
            if not row.get("id") or not row.get("question") or not row.get("target_column"):
                raise ValueError(f"Missing id, question, or target_column in {path} line {line_num}")
            rows.append(row)
    return rows


def load_existing_output(path: Path) -> Dict[str, Dict[str, Any]]:
    if not path.exists():
        return {}

    rows_by_id: Dict[str, Dict[str, Any]] = {}
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            item = json.loads(line)
            if item.get("id"):
                rows_by_id[item["id"]] = item
    return rows_by_id


def save_output(path: Path, rows: List[Dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(to_json_safe(row), ensure_ascii=False) + "\n")


def normalize_sql(sql: str) -> str:
    return " ".join(sql.split()) if sql else ""


def portable_path(path: Path) -> str:
    """Return a path relative to this analysis folder for portable artifacts."""
    return Path(os.path.relpath(path, ANALYSIS_DIR)).as_posix()


def file_sha256(path: Path) -> str:
    import hashlib

    digest = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def git_revision() -> Optional[str]:
    try:
        return subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=PROJECT_ROOT,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return None


def build_run_metadata(prompt_rows: List[Dict[str, Any]], mode: str, repeats: int = 1) -> Dict[str, Any]:
    from config import EMBEDDING_SOURCE, LLM_SOURCE
    import config

    source_csv = ANALYSIS_DIR / "data" / "source" / "adverse_event_report_dataframe.csv"
    return {
        "schema_version": 1,
        "created_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "mode": mode,
        "repeats": repeats,
        "git_revision": git_revision(),
        "python_version": platform.python_version(),
        "platform": platform.platform(),
        "pandas_version": pd.__version__,
        "llm_source": LLM_SOURCE.name,
        "llm_model_setting": getattr(config, f"{LLM_SOURCE.name}_MODEL", None),
        "anthropic_model_override": os.environ.get("ANTHROPIC_MODEL"),
        "embedding_source": EMBEDDING_SOURCE.name,
        "prompt_ids": [row["id"] for row in prompt_rows],
        "prompt_file": portable_path(DEFAULT_PROMPTS),
        "prompt_sha256": file_sha256(DEFAULT_PROMPTS),
        "source_file": portable_path(source_csv),
        "source_sha256": file_sha256(source_csv),
    }


def save_run_metadata(path: Path, metadata: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(metadata, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


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


def sanitize_generated_dataframe(result_df: pd.DataFrame, target_column: str) -> pd.DataFrame:
    output_df = result_df.copy()
    output_df.columns = [str(column).strip() for column in output_df.columns]

    stripped_target = target_column.strip()
    matching_columns = [column for column in output_df.columns if column.strip().lower() == stripped_target.lower()]
    if matching_columns and matching_columns[0] != target_column:
        output_df = output_df.rename(columns={matching_columns[0]: target_column})

    for column in output_df.columns:
        if output_df[column].dtype == object:
            output_df[column] = output_df[column].map(
                lambda value: " ".join(value.splitlines()) if isinstance(value, str) else value
            )
    return output_df


def update_result_row(
    row: Dict[str, Any],
    result_df: Optional[pd.DataFrame],
    sql_query: str,
    error: Optional[str],
    df_dir: Path,
) -> None:
    row["generated_sql"] = normalize_sql(sql_query)
    row["generated_at"] = datetime.now().isoformat(timespec="seconds")
    row["generated_error"] = error

    if isinstance(result_df, pd.DataFrame):
        result_df = sanitize_generated_dataframe(result_df, row["target_column"])
        df_dir.mkdir(parents=True, exist_ok=True)
        csv_path = df_dir / f"{row['id']}.csv"
        result_df.to_csv(csv_path, index=False)
        row["generated_df_csv"] = portable_path(csv_path)
        row["generated_df_preview"] = dataframe_preview(result_df, max_rows=5)
    else:
        row["generated_df_csv"] = None
        row["generated_df_preview"] = {"row_count": None, "preview_rows": []}


def select_prompt_rows(
    rows: List[Dict[str, Any]],
    prompt_id: Optional[str],
    target_column: Optional[str],
    question: Optional[str],
) -> List[Dict[str, Any]]:
    if question:
        return [
            {
                "id": prompt_id or "custom_question",
                "target_column": target_column or "derived_column",
                "question": question,
            }
        ]

    selected = rows
    if prompt_id:
        selected = [row for row in selected if row["id"] == prompt_id]
    if target_column:
        selected = [row for row in selected if row["target_column"].strip().lower() == target_column.strip().lower()]
    if not selected:
        raise ValueError("No matching adverse-event column prompt found.")
    return selected


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run BUMP adverse-event derived-column prompts and save generated dataframes."
    )
    parser.add_argument("--prompts", type=Path, default=DEFAULT_PROMPTS)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--df-dir", type=Path, default=DEFAULT_DF_DIR)
    parser.add_argument("--metadata-output", type=Path, default=DEFAULT_METADATA_OUTPUT)
    parser.add_argument("--id", help="Run only one prompt id from adverse_event_column_prompts.jsonl.")
    parser.add_argument("--target-column", help="Run only one target column, or name the target for --question.")
    parser.add_argument("--question", help="Run one ad hoc natural-language question.")
    parser.add_argument("--in-memory", action="store_true", help="Reload BUMP into an in-memory SQLite database.")
    parser.add_argument("--rerun-tested", action="store_true", help="Rerun prompts that already have saved output.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    all_prompt_rows = load_prompt_rows(args.prompts)
    prompt_rows = select_prompt_rows(all_prompt_rows, args.id, args.target_column, args.question)
    selected_prompt_ids = {row["id"] for row in prompt_rows}

    os.environ.setdefault("MPLCONFIGDIR", str(DEFAULT_MPLCONFIGDIR))
    DEFAULT_MPLCONFIGDIR.mkdir(parents=True, exist_ok=True)
    import query_system as uqs

    existing_output = load_existing_output(args.output)
    output_rows: List[Dict[str, Any]] = []
    rows_to_write = prompt_rows if args.question else all_prompt_rows
    for row in rows_to_write:
        merged = dict(row)
        if row["id"] in existing_output:
            merged.update(existing_output[row["id"]])
            merged.update(row)
        output_rows.append(merged)

    engine = uqs.create_in_memory_db_engine(uqs.SCOPE_BUMP) if args.in_memory else uqs.create_shared_db_engine()
    retrieval_context = uqs.initialize_retrieval_context(engine, uqs.SCOPE_BUMP)
    schema_retriever = retrieval_context["schema_retriever"]
    kb_retriever = retrieval_context["kb_retriever"]
    save_run_metadata(args.metadata_output, build_run_metadata(prompt_rows, mode="single_run"))

    completed = 0
    skipped = 0
    for row in output_rows:
        if row["id"] not in selected_prompt_ids:
            continue

        already_tested = bool((row.get("generated_sql") or "").strip() or row.get("generated_error"))
        if already_tested and not args.rerun_tested:
            skipped += 1
            continue

        result_df = None
        sql_query = ""
        error = None
        metrics = QueryMetrics()

        print(f"\n=== Running adverse-event derived-column prompt: {row['id']} ===")
        print(row["question"])
        try:
            with patched_input():
                with metrics:
                    result_df, sql_query, _explanation = uqs.process_natural_query(
                        row["question"], engine, schema_retriever, kb_retriever,
                        impute="no", session_scope=uqs.SCOPE_BUMP,
                    )
        except Exception as exc:
            error = str(exc)
            print(f"Prompt failed: {error}")

        update_result_row(row, result_df, sql_query, error, args.df_dir)
        row.update(metrics.as_dict())
        save_output(args.output, output_rows)
        completed += 1

    print(f"\nCompleted: {completed}")
    print(f"Skipped existing: {skipped}")
    print(f"Results written to: {args.output}")
    print(f"Generated dataframe CSVs written to: {args.df_dir}")


if __name__ == "__main__":
    main()

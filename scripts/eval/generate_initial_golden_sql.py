"""
Batch runner that feeds prompt questions into query_system.py and
writes the generated SQL back into the `sql` field for each line.

Usage:
    python scripts/eval/generate_initial_golden_sql.py
    python scripts/eval/generate_initial_golden_sql.py prompts/curated/bump_prompts.jsonl --dataset-scope BUMP
    python scripts/eval/generate_initial_golden_sql.py --prompts-path prompts/curated/sinc_prompts.jsonl

Notes:
- Defaults to the SinC prompt set.
- If dataset scope is not provided, the script tries to infer it from the
  prompt filename and otherwise falls back to SinC.
- This script monkeypatches `input` so query_system can run
  unattended:
  - ambiguity prompts auto-select option 1
  - refinement prompts press Enter to accept the generated SQL
- It uses the same database setup and retrieval initialization as the current
  interactive querying system.
"""

import argparse
import builtins
import json
import pathlib
import sys
from typing import Dict, List

# Ensure project root is on sys.path so query_system.py can be imported.
PROJECT_ROOT = pathlib.Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import query_system as uqs


DEFAULT_PROMPTS_PATH = "prompts/curated/sinc_prompts.jsonl"


def _init_engine_and_retrievers(session_scope: str):
    """Replicate the current interactive setup for the selected dataset scope."""
    engine = uqs.create_in_memory_db_engine(session_scope)
    retrieval_context = uqs.initialize_retrieval_context(engine, session_scope)
    return (
        engine,
        retrieval_context["schema_retriever"],
        retrieval_context["kb_retriever"],
    )


def _load_prompts(path: pathlib.Path) -> List[Dict[str, str]]:
    with path.open("r", encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def _save_prompts(path: pathlib.Path, rows: List[Dict[str, str]]):
    with path.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row) + "\n")


def _auto_input(prompt: str = "") -> str:
    """Run unattended while preserving the current system's interaction flow."""
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


def _run_question(question: str, engine, schema_retriever, kb_retriever, session_scope: str) -> str:
    """Run a single question through process_natural_query and return SQL."""
    original_input = builtins.input
    builtins.input = _auto_input
    try:
        _, sql_query, _ = uqs.process_natural_query(
            question,
            engine,
            schema_retriever,
            kb_retriever,
            impute="no",
            session_scope=session_scope,
        )
        return sql_query
    finally:
        builtins.input = original_input


def _normalize_sql(sql: str) -> str:
    """Collapse whitespace/newlines so SQL is stored on a single clean line."""
    return " ".join(sql.split())


def _infer_scope_from_prompts_path(prompts_path: pathlib.Path) -> str:
    """Infer dataset scope from common prompt filenames."""
    normalized_name = prompts_path.name.lower()

    if "sinc" in normalized_name:
        return uqs.SCOPE_SINC
    if "researchers" in normalized_name or "survey" in normalized_name:
        return uqs.SCOPE_BUMP

    return uqs.SCOPE_SINC


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Batch-run questions through query_system and store SQL back into the jsonl file.")
    parser.add_argument(
        "prompt_file",
        nargs="?",
        default=DEFAULT_PROMPTS_PATH,
        help=f"Path to prompts jsonl (default: {DEFAULT_PROMPTS_PATH})",
    )
    parser.add_argument(
        "--prompts-path",
        dest="prompts_path",
        help="Explicit path to prompts jsonl. Overrides the positional prompt file if provided.",
    )
    parser.add_argument(
        "--dataset-scope",
        choices=["BUMP", "SinC", "Cross-dataset", "bump", "sinc", "cross-dataset"],
        help="Dataset scope to use for SQL generation. If omitted, inferred from the prompt filename when possible.",
    )
    return parser.parse_args()


def main():
    args = _parse_args()
    prompts_path_str = args.prompts_path or args.prompt_file
    prompts_path = pathlib.Path(prompts_path_str)

    if args.dataset_scope:
        session_scope = uqs.normalize_scope_name(args.dataset_scope)
    else:
        session_scope = _infer_scope_from_prompts_path(prompts_path)

    print(f"Using prompts file: {prompts_path}")
    print(f"Using dataset scope: {session_scope}")

    engine, schema_retriever, kb_retriever = _init_engine_and_retrievers(session_scope)
    rows = _load_prompts(prompts_path)

    for row in rows:
        question = row.get("question", "").strip()
        # Check if SQL already exists
        existing_sql = row.get("sql", "").strip()

        if not question:
            continue

        # Skip if we already have a SQL query
        if existing_sql:
            print(f"Skipping (SQL already exists): {question[:50]}...")
            continue

        print(f"\n=== Running question ===\n{question}\n")
        try:
            sql = _run_question(
                question,
                engine,
                schema_retriever,
                kb_retriever,
                session_scope,
            )
            row["sql"] = _normalize_sql(sql)
            row.pop("error", None)
        except Exception as exc:
            print(
                f"Failed to generate SQL for question: {question}\nError: {exc}")
            row["error"] = str(exc)

    _save_prompts(prompts_path, rows)
    print(f"\nDone. Updated SQL written to {prompts_path}.")


if __name__ == "__main__":
    main()

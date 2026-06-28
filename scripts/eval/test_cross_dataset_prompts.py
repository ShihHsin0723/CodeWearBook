import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, List

# Ensure project root is importable when running this script directly.
PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import query_system as uqs
from scripts.eval.test_simulated_prompts import (
    ABLATION_CHOICES,
    append_case_result,
    extract_tested_keys,
    initialize_querying_context,
    load_existing_cases_from_summary,
    _normalize_question,
    load_prompts,
    run_case,
    summarize,
    to_json_safe,
    write_summary,
)


DEFAULT_PROMPTS = Path("prompts/simulated/cross_dataset_prompts.jsonl")
DEFAULT_OUTPUT = Path("scripts/eval/results/simulated/cross_dataset_results.jsonl")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Evaluate cross-dataset simulated prompts against query_system.py"
    )
    parser.add_argument(
        "--prompts",
        type=Path,
        default=DEFAULT_PROMPTS,
        help=f"Path to cross-dataset prompt JSONL (default: {DEFAULT_PROMPTS}).",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_OUTPUT,
        help=f"Path to write per-case results JSONL (default: {DEFAULT_OUTPUT}).",
    )
    parser.add_argument(
        "--max-cases",
        type=int,
        default=None,
        help="Max number of prompts to run.",
    )
    parser.add_argument(
        "--start-index",
        type=int,
        default=0,
        help="Start offset in prompts file.",
    )
    parser.add_argument(
        "--shared-db",
        action="store_true",
        help="Use shared DB connection string; default is in-memory DB loading.",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Show querying system logs for each case.",
    )
    parser.add_argument(
        "--ablation",
        choices=ABLATION_CHOICES,
        default="baseline",
        help="Run one ablation variant. Use baseline for the full system.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    session_scope = uqs.SCOPE_CROSS
    if args.ablation == "baseline":
        os.environ.pop(uqs.ABLATION_ENV_VAR, None)
    else:
        os.environ[uqs.ABLATION_ENV_VAR] = args.ablation
    if args.output == DEFAULT_OUTPUT and args.ablation != "baseline":
        args.output = DEFAULT_OUTPUT.with_name(
            f"{DEFAULT_OUTPUT.stem}_{args.ablation}{DEFAULT_OUTPUT.suffix}"
        )
    summary_path = args.output.with_suffix(".summary.json")

    existing_cases = load_existing_cases_from_summary(summary_path)
    tested_keys = extract_tested_keys(existing_cases)

    prompts = load_prompts(args.prompts)
    indexed_subset = list(
        enumerate(prompts[args.start_index:], start=args.start_index)
    )
    if args.max_cases is not None:
        indexed_subset = indexed_subset[: args.max_cases]
    subset = [
        (idx, row)
        for idx, row in indexed_subset
        if (
            f"id:{row['id']}" if isinstance(row.get("id"), str) and row["id"] else
            f"question:{_normalize_question(row.get('question'))}"
        ) not in tested_keys
    ]

    if tested_keys:
        print(
            f"Found {len(tested_keys)} tested prompts in existing summary: {summary_path}"
        )
    print(f"Running {len(subset)} cross-dataset prompt(s) after id/question-based skip.")

    if args.shared_db:
        engine = uqs.create_shared_db_engine()
    else:
        engine = uqs.create_in_memory_db_engine(session_scope)

    retrieval_context = initialize_querying_context(engine, session_scope)
    schema_retriever = retrieval_context["schema_retriever"]
    kb_retriever = retrieval_context["kb_retriever"]

    results: List[Dict[str, Any]] = []
    errored_cases: List[Dict[str, Any]] = []
    for idx, row in subset:
        result = run_case(
            row,
            engine,
            schema_retriever,
            kb_retriever,
            session_scope,
            quiet=not args.verbose,
        )
        result["index"] = idx
        result["tables"] = row.get("tables")
        print(
            f"[{idx}] sql_match={result['sql_match']} "
            f"dataframe_match={result['dataframe_match']} "
            f"df_match_score={result['df_match_score']:.3f} "
            f"error={'yes' if result['error'] else 'no'}"
        )
        if result["error"]:
            errored_cases.append(
                {
                    "index": idx,
                    "id": result.get("id"),
                    "error": result["error"],
                }
            )
        results.append(result)
        append_case_result(args.output, result)
        write_summary(summary_path, existing_cases + results)

    summary = summarize(existing_cases + results)
    summary_safe = to_json_safe(summary)

    print("\nSummary:")
    summary_for_console = {
        k: v
        for k, v in summary_safe.items()
        if k not in {"sql_match_cases", "sql_mismatch_cases"}
    }
    print(json.dumps(summary_for_console, indent=2))

    write_summary(summary_path, existing_cases + results)
    print(f"\nWrote case results to: {args.output}")
    print(f"Wrote summary to: {summary_path}")
    if errored_cases:
        print(
            "Cases with errors (not recorded): "
            + ", ".join(str(case["index"]) for case in errored_cases)
        )
        print("Error details for unrecorded cases:")
        for case in errored_cases:
            print(
                f"- [{case['index']}] id={case.get('id') or 'N/A'} error={case['error']}"
            )


if __name__ == "__main__":
    main()

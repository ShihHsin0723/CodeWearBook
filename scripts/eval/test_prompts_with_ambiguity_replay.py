import argparse
import builtins
import contextlib
import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import query_system as uqs
from llm_metrics import QueryMetrics
from scripts.eval.prompt_eval_utils import (
    canonicalize_sql,
    dataframe_match_score,
    dataframe_preview,
    to_json_safe,
)
from scripts.eval.test_simulated_prompts import (
    ABLATION_CHOICES,
    dedupe_case_results,
    initialize_querying_context,
    load_prompts,
    prompt_result_key,
    summarize,
    write_summary,
)


FULL_SYSTEM_VARIANT = "full_system"
CLI_ABLATION_CHOICES = [
    FULL_SYSTEM_VARIANT,
    *[choice for choice in ABLATION_CHOICES if choice != "baseline"],
]
DEFAULT_SINC_ABLATION_DIR = Path("scripts/eval/sinc_ablation")
DEFAULT_CHOICE_DIR = DEFAULT_SINC_ABLATION_DIR


def normalize_cli_ablation(value: str) -> str:
    return "baseline" if value == FULL_SYSTEM_VARIANT else value


def display_ablation_name(value: str) -> str:
    return FULL_SYSTEM_VARIANT if value == "baseline" else value


def default_paths(session_scope: str, ablation: str) -> Dict[str, Path]:
    display_name = display_ablation_name(ablation)
    if session_scope == uqs.SCOPE_SINC:
        stem = f"sinc_curated_{display_name}_ambiguity_replay"
        return {
            "prompts": Path("prompts/curated/sinc_prompts.jsonl"),
            "output": DEFAULT_SINC_ABLATION_DIR / f"{stem}.jsonl",
            "choices": DEFAULT_SINC_ABLATION_DIR / "sinc_ambiguity_choices.json",
        }
    if session_scope == uqs.SCOPE_CROSS:
        stem = f"cross_dataset_{display_name}_ambiguity_replay"
        return {
            "prompts": Path("prompts/simulated/cross_dataset_prompts.jsonl"),
            "output": Path("scripts/eval/results/ablation") / f"{stem}.jsonl",
            "choices": DEFAULT_CHOICE_DIR / "cross_dataset.json",
        }

    stem = f"bump_{display_name}_ambiguity_replay"
    return {
        "prompts": Path("prompts/simulated/bump_prompts.jsonl"),
        "output": Path("scripts/eval/results/ablation") / f"{stem}.jsonl",
        "choices": DEFAULT_CHOICE_DIR / "bump.json",
    }


def load_choice_store(path: Path) -> Dict[str, Any]:
    if not path.exists():
        return {"version": 1, "choices": {}}
    with path.open("r", encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict):
        raise ValueError(f"Ambiguity choice file must contain a JSON object: {path}")
    data.setdefault("version", 1)
    data.setdefault("choices", {})
    if not isinstance(data["choices"], dict):
        raise ValueError(f"Ambiguity choice file has invalid 'choices': {path}")
    return data


def save_choice_store(path: Path, store: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(to_json_safe(store), indent=2, ensure_ascii=False, allow_nan=False),
        encoding="utf-8",
    )


def load_case_results(path: Path) -> List[Dict[str, Any]]:
    if not path.exists():
        return []
    rows: List[Dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return dedupe_case_results_by_index(rows)


def case_identity(row: Dict[str, Any]) -> Tuple[str, Any]:
    index = row.get("index")
    if isinstance(index, int):
        return ("index", index)
    return ("prompt", prompt_result_key(row))


def dedupe_case_results_by_index(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    deduped: Dict[Tuple[str, Any], Dict[str, Any]] = {}
    for row in rows:
        deduped[case_identity(row)] = row
    return list(deduped.values())


def case_sort_key(row: Dict[str, Any]) -> Tuple[int, Any]:
    index = row.get("index")
    if isinstance(index, int):
        return (0, index)
    return (1, prompt_result_key(row))


def write_case_results(path: Path, rows: List[Dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    deduped = sorted(dedupe_case_results_by_index(rows), key=case_sort_key)
    with path.open("w", encoding="utf-8") as f:
        for row in deduped:
            f.write(json.dumps(to_json_safe(row), ensure_ascii=False, allow_nan=False) + "\n")


def parse_case_indexes(raw: Optional[str]) -> Optional[Set[int]]:
    if raw is None or not raw.strip():
        return None

    selected: Set[int] = set()
    for part in raw.split(","):
        item = part.strip()
        if not item:
            continue
        if "-" in item:
            start_text, end_text = [piece.strip() for piece in item.split("-", 1)]
            if not start_text.isdigit() or not end_text.isdigit():
                raise ValueError(f"Invalid --cases range: {item}")
            start = int(start_text)
            end = int(end_text)
            if end < start:
                raise ValueError(f"Invalid --cases range with descending bounds: {item}")
            selected.update(range(start, end + 1))
            continue
        if not item.isdigit():
            raise ValueError(f"Invalid --cases item: {item}")
        selected.add(int(item))
    return selected


def sanitize_resolution(result: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if not isinstance(result, dict):
        return None
    keep_keys = [
        "resolution_type",
        "selected_tables",
        "table",
        "option_label",
        "option_description",
        "reason",
        "clarification_comments",
    ]
    sanitized = {key: result[key] for key in keep_keys if key in result}
    selected_tables = sanitized.get("selected_tables") or []
    if selected_tables and "table" not in sanitized and len(selected_tables) == 1:
        sanitized["table"] = selected_tables[0]
    return sanitized


def replay_resolution_from_choice(choice: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    resolution = choice.get("resolution")
    if not isinstance(resolution, dict):
        return None
    selected_tables = list(resolution.get("selected_tables") or [])
    resolution_type = resolution.get("resolution_type")
    if not resolution_type:
        resolution_type = "multi_table" if len(selected_tables) > 1 else "single_table"
    if resolution_type == "single_table" and not selected_tables:
        table = resolution.get("table")
        if table:
            selected_tables = [table]
    replayed = {
        "resolution_type": resolution_type,
        "selected_tables": selected_tables,
        "option_label": resolution.get("option_label") or "Replayed ambiguity choice",
        "option_description": resolution.get("option_description") or "Loaded from ambiguity choice file.",
    }
    if resolution_type == "single_table" and selected_tables:
        replayed["table"] = resolution.get("table") or selected_tables[0]
    if resolution_type == "no_match":
        replayed["option_description"] = (
            resolution.get("option_description")
            or "Replayed no-match ambiguity choice."
        )
    return replayed


@contextlib.contextmanager
def patched_input(manual_ambiguity: bool):
    original_input = builtins.input

    def _input(prompt: str = "") -> str:
        normalized = prompt.lower()
        if manual_ambiguity and (
            "select one or more options" in normalized
            or "describe the data you want" in normalized
            or "describe the table or fields you expected" in normalized
            or "select option 1 or 2" in normalized
            or "enter expected collection frequency" in normalized
        ):
            return original_input(prompt)
        if "select one or more options" in normalized:
            return "1"
        if "describe the data you want" in normalized:
            return ""
        if "describe the table or fields you expected" in normalized:
            return ""
        if "[feedback]" in normalized:
            return ""
        if "select option 1 or 2" in normalized:
            return "2"
        return ""

    builtins.input = _input
    try:
        yield
    finally:
        builtins.input = original_input


@contextlib.contextmanager
def suppress_stdout_stderr(enabled: bool):
    if not enabled:
        yield
        return
    with open("/dev/null", "w", encoding="utf-8") as devnull:
        with contextlib.redirect_stdout(devnull), contextlib.redirect_stderr(devnull):
            yield


@contextlib.contextmanager
def ambiguity_replay_context(
    choice_store: Dict[str, Any],
    choice_path: Path,
    prompt_key: str,
    question: str,
    session_scope: str,
    record_choices: bool,
    replay_choices: bool,
    force_prompt: bool,
):
    original_detect = uqs.detect_and_resolve_ambiguity
    original_single = uqs._should_accept_authoritative_single_table
    original_multi = uqs._should_accept_authoritative_multi_table

    def _detect_with_replay(natural_query, schema_retriever, max_options=3, max_retries=1, session_scope=None):
        choices = choice_store.setdefault("choices", {})
        existing_choice = choices.get(prompt_key)
        if replay_choices and existing_choice:
            replayed = replay_resolution_from_choice(existing_choice)
            if replayed is not None:
                print(
                    "Replaying ambiguity choice for "
                    f"{prompt_key}: {', '.join(replayed.get('selected_tables') or []) or replayed['resolution_type']}"
                )
                return replayed

        result = original_detect(
            natural_query,
            schema_retriever,
            max_options=max_options,
            max_retries=max_retries,
            session_scope=session_scope,
        )
        if record_choices:
            resolution = sanitize_resolution(result)
            if resolution is not None:
                choices[prompt_key] = {
                    "question": question,
                    "dataset_scope": session_scope,
                    "resolution": resolution,
                }
                save_choice_store(choice_path, choice_store)
                print(f"Recorded ambiguity choice for {prompt_key} in {choice_path}")
        return result

    uqs.detect_and_resolve_ambiguity = _detect_with_replay
    if force_prompt:
        uqs._should_accept_authoritative_single_table = lambda *args, **kwargs: False
        uqs._should_accept_authoritative_multi_table = lambda *args, **kwargs: False
    try:
        yield
    finally:
        uqs.detect_and_resolve_ambiguity = original_detect
        uqs._should_accept_authoritative_single_table = original_single
        uqs._should_accept_authoritative_multi_table = original_multi


def run_case_with_replay(
    row: Dict[str, Any],
    idx: int,
    engine,
    schema_retriever,
    kb_retriever,
    session_scope: str,
    choice_store: Dict[str, Any],
    choice_path: Path,
    record_choices: bool,
    manual_ambiguity: bool,
    force_prompt: bool,
    quiet: bool,
) -> Dict[str, Any]:
    question = row["question"]
    expected_sql = row["sql"]
    prompt_key = prompt_result_key(row)

    if manual_ambiguity:
        print("\n" + "=" * 80)
        print(f"Prompt [{idx}] {prompt_key}")
        print(question)
        print("=" * 80)

    generated_sql: Optional[str] = None
    raw_sql_match = False
    df_match = False
    df_match_score = 0.0
    error: Optional[str] = None
    expected_df_preview: Dict[str, Any] = {"row_count": None, "preview_rows": []}
    generated_df_preview: Dict[str, Any] = {"row_count": None, "preview_rows": []}
    metrics = QueryMetrics()

    try:
        with ambiguity_replay_context(
            choice_store,
            choice_path,
            prompt_key,
            question,
            session_scope,
            record_choices=record_choices,
            replay_choices=not manual_ambiguity,
            force_prompt=force_prompt,
        ):
            with patched_input(manual_ambiguity=manual_ambiguity):
                with suppress_stdout_stderr(quiet):
                    with metrics:
                        generated_df, generated_sql, _ = uqs.process_natural_query(
                            question, engine, schema_retriever, kb_retriever,
                            impute="no", session_scope=session_scope,
                        )

        raw_sql_match = canonicalize_sql(generated_sql) == canonicalize_sql(expected_sql)
        generated_df_preview = dataframe_preview(generated_df, max_rows=5)
        if raw_sql_match:
            df_match = True
            df_match_score = 1.0
            expected_df_preview = generated_df_preview
        else:
            expected_df = pd.read_sql(expected_sql, engine)
            expected_df_preview = dataframe_preview(expected_df, max_rows=5)
            if isinstance(generated_df, pd.DataFrame):
                df_match_score = dataframe_match_score(expected_df, generated_df)
                df_match = df_match_score == 1.0
    except Exception as exc:  # noqa: BLE001
        error = str(exc)

    return {
        "id": row.get("id"),
        "table": row.get("table"),
        "question": question,
        "ablation": display_ablation_name(os.environ.get(uqs.ABLATION_ENV_VAR, "baseline") or "baseline"),
        "raw_sql_match": raw_sql_match,
        "expected_sql": expected_sql,
        "generated_sql": generated_sql,
        "sql_match": raw_sql_match,
        "dataframe_match": df_match,
        "df_match_score": df_match_score,
        "expected_df_preview": expected_df_preview,
        "generated_df_preview": generated_df_preview,
        "error": error,
        "index": idx,
        **metrics.as_dict(),
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Evaluate prompts while recording or replaying manual ambiguity choices."
    )
    parser.add_argument("--dataset-scope", default="sinc", help="Dataset scope: sinc, bump, or cross.")
    parser.add_argument("--prompts", type=Path, default=None, help="Prompt JSONL with question/sql.")
    parser.add_argument("--output", type=Path, default=None, help="Per-case result JSONL.")
    parser.add_argument("--ambiguity-choices", type=Path, default=None, help="JSON file for ambiguity choices.")
    parser.add_argument("--record-ambiguity-choices", action="store_true", help="Save choices made during this run.")
    parser.add_argument(
        "--manual-ambiguity",
        action="store_true",
        help=(
            "Let you type ambiguity selections instead of auto-selecting option 1. "
            "Existing saved choices are ignored in this mode."
        ),
    )
    parser.add_argument("--force-ambiguity-prompt", action="store_true", help="Disable authoritative auto-accept so ambiguous cases prompt for selection.")
    parser.add_argument(
        "--cases",
        default=None,
        help=(
            "Prompt indexes to run, e.g. '1-20' or '1,5,10'. "
            "Selected cases are always rerun and overwrite existing rows."
        ),
    )
    parser.add_argument("--max-cases", type=int, default=None)
    parser.add_argument("--start-index", type=int, default=0)
    parser.add_argument("--shared-db", action="store_true")
    parser.add_argument("--verbose", action="store_true")
    parser.add_argument("--rerun-tested", action="store_true", help="Ignore existing summary skip state.")
    parser.add_argument(
        "--ablation",
        choices=CLI_ABLATION_CHOICES,
        default=FULL_SYSTEM_VARIANT,
        help=(
            "Variant to run. Use full_system for the complete system; other values "
            "disable one component for ablation."
        ),
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    session_scope = uqs.normalize_scope_name(args.dataset_scope)
    internal_ablation = normalize_cli_ablation(args.ablation)
    selected_case_indexes = parse_case_indexes(args.cases)

    if internal_ablation == "baseline":
        os.environ.pop(uqs.ABLATION_ENV_VAR, None)
    else:
        os.environ[uqs.ABLATION_ENV_VAR] = internal_ablation

    paths = default_paths(session_scope, internal_ablation)
    args.prompts = args.prompts or paths["prompts"]
    args.output = args.output or paths["output"]
    args.ambiguity_choices = args.ambiguity_choices or paths["choices"]

    if internal_ablation == uqs.ABLATION_NO_AMBIGUITY_RESOLVER:
        print(
            "Warning: no_ambiguity_resolver bypasses ambiguity detection, "
            "so replayed/manual ambiguity choices will not be used."
        )

    choice_store = load_choice_store(args.ambiguity_choices)
    summary_path = args.output.with_suffix(".summary.json")
    existing_cases = [] if args.rerun_tested else load_case_results(args.output)
    tested_keys = {
        prompt_result_key(case)
        for case in existing_cases
        if not case.get("error")
    }

    prompts = load_prompts(args.prompts)
    indexed_subset = list(enumerate(prompts[args.start_index:], start=args.start_index))
    if args.max_cases is not None:
        indexed_subset = indexed_subset[: args.max_cases]
    if selected_case_indexes is not None:
        indexed_subset = [
            (idx, row)
            for idx, row in indexed_subset
            if idx in selected_case_indexes
        ]
    subset = [
        (idx, row)
        for idx, row in indexed_subset
        if selected_case_indexes is not None or prompt_result_key(row) not in tested_keys
    ]

    if tested_keys:
        print(f"Found {len(tested_keys)} tested prompts in existing results: {args.output}")
    if selected_case_indexes is not None:
        missing = sorted(selected_case_indexes - {idx for idx, _row in indexed_subset})
        if missing:
            print(f"Requested case indexes not found in selected prompt range: {missing}")
    print(f"Using ambiguity choices: {args.ambiguity_choices}")
    print(f"Running {len(subset)} prompt(s) for variant={display_ablation_name(internal_ablation)}.")

    engine = uqs.create_shared_db_engine() if args.shared_db else uqs.create_in_memory_db_engine(session_scope)
    retrieval_context = initialize_querying_context(engine, session_scope)
    schema_retriever = retrieval_context["schema_retriever"]
    kb_retriever = retrieval_context["kb_retriever"]

    results: List[Dict[str, Any]] = []
    all_results = list(existing_cases)
    quiet = not args.verbose and not args.manual_ambiguity
    for idx, row in subset:
        result = run_case_with_replay(
            row,
            idx,
            engine,
            schema_retriever,
            kb_retriever,
            session_scope,
            choice_store,
            args.ambiguity_choices,
            record_choices=args.record_ambiguity_choices,
            manual_ambiguity=args.manual_ambiguity,
            force_prompt=args.force_ambiguity_prompt,
            quiet=quiet,
        )
        print(
            f"[{idx}] sql_match={result['sql_match']} "
            f"dataframe_match={result['dataframe_match']} "
            f"df_match_score={result['df_match_score']:.3f} "
            f"error={'yes' if result['error'] else 'no'}"
        )
        results.append(result)
        all_results = [
            existing
            for existing in all_results
            if case_identity(existing) != case_identity(result)
        ]
        all_results.append(result)
        write_case_results(args.output, all_results)
        write_summary(summary_path, all_results)

    combined = dedupe_case_results_by_index(all_results)
    summary = summarize(combined)
    write_case_results(args.output, combined)
    write_summary(summary_path, combined)
    console_summary = {
        k: v for k, v in to_json_safe(summary).items()
        if k not in {"sql_match_cases", "sql_mismatch_cases"}
    }
    print(json.dumps(console_summary, indent=2, ensure_ascii=False))
    print(f"\nWrote case results to: {args.output}")
    print(f"Wrote summary to: {summary_path}")
    print(f"Wrote ambiguity choices to: {args.ambiguity_choices}")


if __name__ == "__main__":
    main()

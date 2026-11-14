import argparse
import builtins
import contextlib
import json
import math
import os
import re
import sys
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Set

import pandas as pd
from sqlalchemy import create_engine

# Ensure project root is importable when running this script directly.
PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from config import DB_CONNECTION_STRING
from llm_metrics import QueryMetrics
from scripts.eval.summarize_efficiency import DEFAULT_PRICES, summarize_rows
from dataset_registry import (
    SCOPE_BUMP,
    SCOPE_CROSS,
    SCOPE_SINC,
    create_tables_for_scope,
    load_data_for_scope,
    normalize_scope_name,
)
from scripts.eval.prompt_eval_utils import (
    canonicalize_sql as eval_canonicalize_sql,
    dataframe_match_score,
    dataframe_preview as eval_dataframe_preview,
    to_json_safe as eval_to_json_safe,
)

ABLATION_ENV_VAR = "SINC_ABLATION"
ABLATION_NO_SCHEMA_KEYWORDS = "no_schema_keywords"
ABLATION_NO_ALL_SCHEMA_KEYWORDS = "no_all_schema_keywords"
ABLATION_NO_KB = "no_kb"
ABLATION_NO_AMBIGUITY_RESOLVER = "no_ambiguity_resolver"
ABLATION_NO_MAJORITY_VOTING = "no_majority_voting"
ABLATION_CHOICES = [
    "baseline",
    ABLATION_NO_SCHEMA_KEYWORDS,
    ABLATION_NO_ALL_SCHEMA_KEYWORDS,
    ABLATION_NO_KB,
    ABLATION_NO_AMBIGUITY_RESOLVER,
    ABLATION_NO_MAJORITY_VOTING,
]
_UQS = None


def get_querying_system():
    global _UQS
    if _UQS is None:
        import query_system as uqs  # noqa: PLC0415
        _UQS = uqs
    return _UQS


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


def split_top_level(expr: str, sep: str = ",") -> List[str]:
    parts: List[str] = []
    start = 0
    depth = 0
    in_single = False
    in_double = False
    for i, ch in enumerate(expr):
        if ch == "'" and not in_double:
            in_single = not in_single
        elif ch == '"' and not in_single:
            in_double = not in_double
        elif not in_single and not in_double:
            if ch == "(":
                depth += 1
            elif ch == ")":
                depth -= 1
            elif ch == sep and depth == 0:
                parts.append(expr[start:i].strip())
                start = i + 1
    tail = expr[start:].strip()
    if tail:
        parts.append(tail)
    return parts


def normalize_simple_expression(expr: str) -> str:
    expr = re.sub(
        r'"([A-Za-z_][A-Za-z0-9_.-]*)"',
        lambda m: m.group(1).lower(),
        expr,
    )
    expr = re.sub(r"\s+", " ", expr.strip().lower())
    expr = re.sub(r"\s*([=<>!+\-*/(),])\s*", r"\1", expr)
    return expr


def strip_expression_alias(expr: str) -> str:
    expr = expr.strip()
    # Remove explicit alias: "... AS alias"
    expr = re.sub(r"\s+as\s+[a-zA-Z_][a-zA-Z0-9_]*$",
                  "", expr, flags=re.IGNORECASE)
    # Remove implicit trailing alias: "... alias" (best-effort)
    expr = re.sub(
        r"^(.*[\]\)`\"'\w])\s+[a-zA-Z_][a-zA-Z0-9_]*$",
        r"\1",
        expr,
        flags=re.IGNORECASE,
    )
    return expr


def extract_table_alias_map(sql: str) -> Dict[str, str]:
    alias_map: Dict[str, str] = {}
    pattern = re.compile(
        r"\b(from|join)\s+([a-zA-Z_][\w\.]*)(?:\s+(?:as\s+)?([a-zA-Z_][\w]*))?",
        flags=re.IGNORECASE,
    )
    for _kw, table_name, alias in pattern.findall(sql):
        if alias and alias.lower() != table_name.lower():
            alias_map[alias.lower()] = table_name.lower()
    return alias_map


def remove_alias_references(expr: str, alias_map: Dict[str, str]) -> str:
    out = expr
    for alias in sorted(alias_map.keys(), key=len, reverse=True):
        out = re.sub(rf"\b{re.escape(alias)}\.", "", out, flags=re.IGNORECASE)
    return out


def split_top_level_bool(expr: str, operator: str) -> List[str]:
    parts: List[str] = []
    start = 0
    depth = 0
    in_single = False
    in_double = False
    operator_lower = operator.lower()
    expr_lower = expr.lower()
    operator_len = len(operator)
    i = 0

    while i < len(expr):
        ch = expr[i]
        if ch == "'" and not in_double:
            in_single = not in_single
            i += 1
            continue
        if ch == '"' and not in_single:
            in_double = not in_double
            i += 1
            continue
        if not in_single and not in_double:
            if ch == "(":
                depth += 1
                i += 1
                continue
            if ch == ")":
                depth -= 1
                i += 1
                continue
            if depth == 0 and expr_lower[i:i + operator_len] == operator_lower:
                prev_ok = i == 0 or not (expr_lower[i - 1].isalnum() or expr_lower[i - 1] == "_")
                next_idx = i + operator_len
                next_ok = next_idx >= len(expr) or not (expr_lower[next_idx].isalnum() or expr_lower[next_idx] == "_")
                if prev_ok and next_ok:
                    parts.append(expr[start:i].strip())
                    start = next_idx
                    i = next_idx
                    continue
        i += 1

    tail = expr[start:].strip()
    if tail:
        parts.append(tail)
    return parts


def normalize_where_clause(where_clause: str) -> str:
    # Heuristic: sort top-level AND terms to reduce sensitivity to condition order.
    terms = split_top_level_bool(where_clause, "and")
    normalized_terms = [normalize_simple_expression(
        t) for t in terms if t.strip()]
    normalized_terms.sort()
    return " and ".join(normalized_terms)


def normalize_from_clause(from_clause: str) -> str:
    pattern = re.compile(
        r"(^|,\s*|\bjoin\s+)([a-zA-Z_][\w\.]*)(?:\s+(?:as\s+)?([a-zA-Z_][\w]*))(?=(?:\s+\bjoin\b|\s+\bon\b|\s*,|$))",
        flags=re.IGNORECASE,
    )
    normalized = pattern.sub(lambda m: f"{m.group(1)}{m.group(2)}", from_clause)
    return normalize_simple_expression(normalized)


def extract_clause(sql: str, start_keyword: str, end_keywords: Iterable[str]) -> str:
    pattern = re.compile(
        rf"\b{re.escape(start_keyword)}\b(.*?)(?=\b(?:{'|'.join(end_keywords)})\b|$)",
        flags=re.IGNORECASE | re.DOTALL,
    )
    m = pattern.search(sql)
    return m.group(1).strip() if m else ""


def canonicalize_sql(sql: str) -> str:
    """
    Best-effort canonical form for approximate SQL equivalence:
    - ignores whitespace/casing
    - normalizes escaped newline/tab sequences
    - compares SELECT / FROM-JOIN / WHERE / GROUP BY / HAVING
    - ignores aliases (best-effort)
    - sorts SELECT/GROUP BY item lists
    - sorts top-level AND terms in WHERE
    - ignores ORDER BY / LIMIT
    """
    # Normalize escaped sequences that can appear in serialized SQL strings.
    s = sql.replace("\\n", " ").replace("\\t", " ").replace("\\r", " ")
    s = s.strip().strip(";")
    s = re.sub(r"\s+", " ", s)
    s_low = s.lower()
    alias_map = extract_table_alias_map(s_low)

    clause_order = ["select", "from", "where", "group by", "having"]
    next_keywords = {
        "select": ["from", "where", "group by", "having", "order by", "limit"],
        "from": ["where", "group by", "having", "order by", "limit"],
        "where": ["group by", "having", "order by", "limit"],
        "group by": ["having", "order by", "limit"],
        "having": ["order by", "limit"],
    }

    canonical_parts: List[str] = []
    for clause in clause_order:
        content = extract_clause(s_low, clause, next_keywords[clause])
        if not content:
            continue
        content = remove_alias_references(content, alias_map)
        if clause in {"select", "group by"}:
            items = [
                normalize_simple_expression(strip_expression_alias(p))
                for p in split_top_level(content)
            ]
            items.sort()
            content = ",".join(items)
        elif clause == "from":
            # Remove table aliases in FROM/JOIN portions.
            content = normalize_from_clause(content)
        elif clause == "where":
            content = normalize_where_clause(content)
        else:
            content = normalize_simple_expression(content)
        canonical_parts.append(f"{clause}:{content}")

    if not canonical_parts:
        return normalize_simple_expression(s_low)
    return " | ".join(canonical_parts)


def normalize_scalar(value: Any) -> str:
    if pd.isna(value):
        return "__NULL__"
    if isinstance(value, float):
        return f"{value:.10g}"
    return str(value)


def normalize_dataframe(
    df: pd.DataFrame,
    ignore_column_names: bool = False,
) -> pd.DataFrame:
    out = df.copy()
    if ignore_column_names:
        out.columns = [f"col_{idx}" for idx in range(len(out.columns))]
    else:
        out.columns = [str(c).strip().lower() for c in out.columns]

    # Keep duplicates stable while still enabling deterministic ordering.
    seen: Dict[str, int] = {}
    unique_cols: List[str] = []
    for c in out.columns:
        idx = seen.get(c, 0)
        seen[c] = idx + 1
        unique_cols.append(f"{c}__{idx}")
    out.columns = unique_cols

    for col in out.columns:
        out[col] = out[col].map(normalize_scalar)

    out = out.reindex(sorted(out.columns), axis=1)
    if len(out.columns) > 0 and len(out) > 0:
        out = out.sort_values(by=list(out.columns), kind="mergesort")
    out = out.reset_index(drop=True)
    return out


def dataframes_match(
    df1: pd.DataFrame,
    df2: pd.DataFrame,
    ignore_column_names: bool = False,
) -> bool:
    n1 = normalize_dataframe(df1, ignore_column_names=ignore_column_names)
    n2 = normalize_dataframe(df2, ignore_column_names=ignore_column_names)
    return n1.equals(n2)


def dataframe_preview(df: Optional[pd.DataFrame], max_rows: int = 5) -> Dict[str, Any]:
    if not isinstance(df, pd.DataFrame):
        return {"row_count": None, "preview_rows": []}
    head = df.head(max_rows)
    return {
        "row_count": int(len(df)),
        "preview_rows": head.to_dict(orient="records"),
    }


def to_json_safe(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, bool)):
        return value
    if isinstance(value, float):
        if math.isnan(value) or math.isinf(value):
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
        except Exception:  # noqa: BLE001
            pass
    return str(value)


canonicalize_sql = eval_canonicalize_sql
dataframe_preview = eval_dataframe_preview
to_json_safe = eval_to_json_safe


@contextlib.contextmanager
def patched_input():
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

    original_input = builtins.input
    builtins.input = _auto_input
    try:
        yield
    finally:
        builtins.input = original_input


@contextlib.contextmanager
def suppress_stdout_stderr():
    with open("/dev/null", "w", encoding="utf-8") as devnull:
        with contextlib.redirect_stdout(devnull), contextlib.redirect_stderr(devnull):
            yield


def initialize_querying_context(engine, session_scope: str) -> Dict[str, Any]:
    uqs = get_querying_system()
    return uqs.initialize_retrieval_context(engine, session_scope)


def resolve_default_paths(session_scope: str) -> Dict[str, Path]:
    if session_scope == SCOPE_SINC:
        return {
            "prompts": Path("prompts/simulated/sinc_prompts.jsonl"),
            "output": Path("scripts/eval/results/simulated/sinc_results.jsonl"),
        }
    return {
        "prompts": Path("prompts/simulated/bump_prompts.jsonl"),
        "output": Path("scripts/eval/results/simulated/bump_results.jsonl"),
    }


def run_case(
    row: Dict[str, Any],
    engine,
    schema_retriever,
    kb_retriever,
    session_scope: str,
    quiet: bool = True,
) -> Dict[str, Any]:
    question = row["question"]
    expected_sql = row["sql"]

    generated_sql: Optional[str] = None
    raw_sql_match = False
    sql_match = False
    df_match = False
    df_match_score = 0.0
    error: Optional[str] = None
    expected_df_preview: Dict[str, Any] = {
        "row_count": None, "preview_rows": []}
    generated_df_preview: Dict[str, Any] = {
        "row_count": None, "preview_rows": []}
    metrics = QueryMetrics()

    try:
        uqs = get_querying_system()
        with patched_input():
            if quiet:
                with suppress_stdout_stderr():
                    with metrics:
                        generated_df, generated_sql, _ = uqs.process_natural_query(
                            question, engine, schema_retriever, kb_retriever,
                            impute="no", session_scope=session_scope,
                        )
            else:
                with metrics:
                    generated_df, generated_sql, _ = uqs.process_natural_query(
                        question, engine, schema_retriever, kb_retriever,
                        impute="no", session_scope=session_scope,
                    )

        raw_sql_match = eval_canonicalize_sql(
            generated_sql) == eval_canonicalize_sql(expected_sql)
        sql_match = raw_sql_match
        generated_df_preview = eval_dataframe_preview(generated_df, max_rows=5)
        if raw_sql_match:
            df_match = True
            df_match_score = 1.0
            if isinstance(generated_df, pd.DataFrame):
                expected_df_preview = generated_df_preview
            else:
                expected_df = pd.read_sql(expected_sql, engine)
                expected_df_preview = eval_dataframe_preview(expected_df, max_rows=5)
                generated_df_preview = expected_df_preview
        else:
            expected_df = pd.read_sql(expected_sql, engine)
            expected_df_preview = eval_dataframe_preview(expected_df, max_rows=5)
            if isinstance(generated_df, pd.DataFrame):
                df_match_score = dataframe_match_score(expected_df, generated_df)
                df_match = df_match_score == 1.0
    except Exception as exc:  # noqa: BLE001
        error = str(exc)

    return {
        "id": row.get("id"),
        "table": row.get("table"),
        "question": question,
        "ablation": os.environ.get(ABLATION_ENV_VAR, "baseline") or "baseline",
        "raw_sql_match": raw_sql_match,
        "expected_sql": expected_sql,
        "generated_sql": generated_sql,
        "sql_match": sql_match,
        "dataframe_match": df_match,
        "df_match_score": df_match_score,
        "expected_df_preview": expected_df_preview,
        "generated_df_preview": generated_df_preview,
        "error": error,
        **metrics.as_dict(),
    }


def summarize(results: List[Dict[str, Any]]) -> Dict[str, Any]:
    total = len(results)

    def result_sql_match(row: Dict[str, Any]) -> bool:
        if row.get("error"):
            return False
        expected_sql = (row.get("expected_sql") or "").strip()
        generated_sql = (row.get("generated_sql") or "").strip()
        if not expected_sql or not generated_sql:
            return False
        return eval_canonicalize_sql(generated_sql) == eval_canonicalize_sql(expected_sql)

    def result_df_score(row: Dict[str, Any]) -> float:
        return float(row.get("df_match_score", 1.0 if row.get("dataframe_match") else 0.0) or 0.0)

    def result_df_match(row: Dict[str, Any]) -> bool:
        return result_df_score(row) == 1.0

    sql_matches = sum(1 for r in results if result_sql_match(r))
    df_matches = sum(1 for r in results if result_df_match(r))
    df_match_score_sum = sum(
        result_df_score(r)
        for r in results
    )
    both_matches = sum(
        1 for r in results if result_sql_match(r) and result_df_match(r))
    errors = sum(1 for r in results if r.get("error"))

    def ratio(n: int, d: int) -> float:
        return (n / d) if d else 0.0

    case_details: List[Dict[str, Any]] = []
    for r in results:
        case_details.append(
            {
                "index": r.get("index"),
                "id": r.get("id"),
                "table": r.get("table"),
                "question": r.get("question"),
                "ablation": r.get("ablation"),
                "raw_sql_match": result_sql_match(r),
                "sql_match": result_sql_match(r),
                "dataframe_match": result_df_match(r),
                "df_match_score": result_df_score(r),
                "expected_sql": r.get("expected_sql"),
                "generated_sql": r.get("generated_sql"),
                "error": r.get("error"),
                **{key: r.get(key) for key in (
                    "query_elapsed_seconds", "llm_elapsed_seconds", "llm_call_count",
                    "llm_input_tokens", "llm_output_tokens", "llm_total_input_tokens",
                    "llm_total_tokens", "llm_usage_complete", "llm_calls")},
                "expected_df_preview": r.get("expected_df_preview"),
                "generated_df_preview": r.get("generated_df_preview"),
            }
        )

    sql_mismatch_cases = [c for c in case_details if not c["sql_match"]]
    sql_match_cases = [c for c in case_details if c["sql_match"]]

    prices = json.loads(DEFAULT_PRICES.read_text(encoding="utf-8"))["models"]
    return {
        "total": total,
        "total_prompts": total,
        "tested_prompts": total,
        "untested_prompts": 0,
        "ablation": next((r.get("ablation") for r in results if r.get("ablation")), None),
        "sql_match_count": sql_matches,
        "sql_match_rate": ratio(sql_matches, total),
        "sql_match_rate_across_tested": ratio(sql_matches, total),
        "dataframe_match_count": df_matches,
        "dataframe_match_rate": ratio(df_matches, total),
        "df_exact_match_count": df_matches,
        "df_exact_match_rate_across_tested": ratio(df_matches, total),
        "average_df_match_score": ratio(df_match_score_sum, total),
        "average_df_match_score_across_tested": ratio(df_match_score_sum, total),
        "both_match_count": both_matches,
        "both_match_rate": ratio(both_matches, total),
        "error_count": errors,
        "error_rate": ratio(errors, total),
        "efficiency": summarize_rows(results, prices),
        "sql_mismatch_cases": sql_mismatch_cases,
        "sql_match_cases": sql_match_cases,
    }


def load_existing_cases_from_summary(path: Path) -> List[Dict[str, Any]]:
    if not path.exists():
        return []
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    if not isinstance(payload, dict):
        return []

    sql_match_cases = payload.get("sql_match_cases", [])
    sql_mismatch_cases = payload.get("sql_mismatch_cases", [])
    if not isinstance(sql_match_cases, list) or not isinstance(sql_mismatch_cases, list):
        return []

    cases: List[Dict[str, Any]] = []
    for case in [*sql_match_cases, *sql_mismatch_cases]:
        if isinstance(case, dict):
            cases.append(case)
    return cases


def _normalize_question(question: Any) -> str:
    if not isinstance(question, str):
        return ""
    return " ".join(question.split()).strip().lower()


def prompt_result_key(row: Dict[str, Any]) -> str:
    prompt_id = row.get("id")
    if isinstance(prompt_id, str) and prompt_id:
        return f"id:{prompt_id}"
    return f"question:{_normalize_question(row.get('question'))}"


def dedupe_case_results(cases: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    deduped: Dict[str, Dict[str, Any]] = {}
    ordered_keys: List[str] = []
    for case in cases:
        key = prompt_result_key(case)
        if key not in deduped:
            ordered_keys.append(key)
        deduped[key] = case
    return [deduped[key] for key in ordered_keys]


def extract_tested_keys(cases: List[Dict[str, Any]]) -> Set[str]:
    tested: Set[str] = set()
    for case in cases:
        if case.get("error"):
            continue
        tested.add(prompt_result_key(case))
    return tested


def append_case_result(path: Path, row: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(eval_to_json_safe(row), ensure_ascii=False) + "\n")


def write_summary(path: Path, results: List[Dict[str, Any]]) -> None:
    results = dedupe_case_results(results)
    path.write_text(
        json.dumps(
            eval_to_json_safe(summarize(results)),
            indent=2,
            ensure_ascii=False,
            allow_nan=False,
        ),
        encoding="utf-8",
    )


def load_existing_cases_from_jsonl(path: Path) -> List[Dict[str, Any]]:
    if not path.exists():
        return []

    cases: List[Dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                item = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(item, dict):
                cases.append(item)
    return cases


def write_case_results(path: Path, results: List[Dict[str, Any]]) -> None:
    results = dedupe_case_results(results)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for result in results:
            f.write(json.dumps(eval_to_json_safe(result), ensure_ascii=False) + "\n")


def recompute_dataframe_score_from_sql(
    row: Dict[str, Any],
    engine,
) -> Dict[str, Any]:
    scored = dict(row)
    expected_sql = (scored.get("expected_sql") or scored.get("sql") or "").strip()
    generated_sql = (scored.get("generated_sql") or "").strip()

    scored["expected_sql"] = expected_sql
    scored["generated_sql"] = generated_sql

    if scored.get("error") or not expected_sql or not generated_sql:
        scored["dataframe_match"] = False
        scored["df_match_score"] = 0.0
        return scored

    try:
        sql_match = eval_canonicalize_sql(generated_sql) == eval_canonicalize_sql(expected_sql)
        scored["raw_sql_match"] = sql_match
        scored["sql_match"] = sql_match
        expected_df = pd.read_sql(expected_sql, engine)
        if sql_match:
            generated_df = expected_df
            score = 1.0
        else:
            generated_df = pd.read_sql(generated_sql, engine)
            score = dataframe_match_score(expected_df, generated_df)
        scored["df_match_score"] = score
        scored["dataframe_match"] = score == 1.0
        scored["expected_df_preview"] = eval_dataframe_preview(expected_df, max_rows=5)
        scored["generated_df_preview"] = eval_dataframe_preview(generated_df, max_rows=5)
        scored["df_score_error"] = None
    except Exception as exc:  # noqa: BLE001
        scored["dataframe_match"] = False
        scored["df_match_score"] = 0.0
        scored["df_score_error"] = str(exc)

    return scored


def create_eval_db_engine(session_scope: str, shared_db: bool = False):
    if shared_db:
        return create_engine(DB_CONNECTION_STRING)

    engine = create_engine("sqlite:///:memory:", echo=False)
    create_tables_for_scope(engine, session_scope)
    load_data_for_scope(engine, session_scope)
    return engine


def score_existing_results(args: argparse.Namespace, session_scope: str) -> None:
    summary_path = args.output.with_suffix(".summary.json")
    existing_cases = load_existing_cases_from_jsonl(args.output)
    if not existing_cases:
        existing_cases = load_existing_cases_from_summary(summary_path)
    if not existing_cases:
        raise FileNotFoundError(
            f"No existing cases found in {args.output} or {summary_path}."
        )

    engine = create_eval_db_engine(session_scope, shared_db=args.shared_db)

    scored_cases = [
        recompute_dataframe_score_from_sql(case, engine)
        for case in existing_cases
    ]
    scored_cases = dedupe_case_results(scored_cases)

    write_case_results(args.output, scored_cases)
    write_summary(summary_path, scored_cases)

    summary = summarize(scored_cases)
    summary_for_console = {
        k: v
        for k, v in eval_to_json_safe(summary).items()
        if k not in {"sql_match_cases", "sql_mismatch_cases"}
    }
    print(json.dumps(summary_for_console, indent=2))
    print(f"\nUpdated scored case results: {args.output}")
    print(f"Updated summary: {summary_path}")


def main():
    parser = argparse.ArgumentParser(
        description="Evaluate simulated prompts against query_system.py"
    )
    parser.add_argument(
        "--dataset-scope",
        default="bump",
        help="Dataset scope for the run: bump or sinc.",
    )
    parser.add_argument(
        "--prompts",
        type=Path,
        default=None,
        help="Path to JSONL prompts file with 'question' and 'sql'.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Path to write per-case results JSONL.",
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
    parser.add_argument(
        "--score-existing",
        action="store_true",
        help=(
            "Recompute dataframe match scores from existing expected/generated SQL "
            "without calling the querying system or LLM."
        ),
    )
    args = parser.parse_args()

    if args.ablation == "baseline":
        os.environ.pop(ABLATION_ENV_VAR, None)
    else:
        os.environ[ABLATION_ENV_VAR] = args.ablation

    session_scope = normalize_scope_name(args.dataset_scope)
    if session_scope == SCOPE_CROSS:
        raise ValueError("Cross-dataset simulated prompt testing is not supported by this script.")

    default_paths = resolve_default_paths(session_scope)
    if args.prompts is None:
        args.prompts = default_paths["prompts"]
    if args.output is None:
        if args.ablation == "baseline":
            args.output = default_paths["output"]
        else:
            args.output = default_paths["output"].with_name(
                f"{default_paths['output'].stem}_{args.ablation}{default_paths['output'].suffix}"
            )

    if args.score_existing:
        score_existing_results(args, session_scope)
        return

    summary_path = args.output.with_suffix(".summary.json")
    existing_cases = load_existing_cases_from_summary(summary_path)
    tested_keys = extract_tested_keys(existing_cases)

    prompts = load_prompts(args.prompts)
    indexed_subset = list(
        enumerate(prompts[args.start_index:], start=args.start_index))
    if args.max_cases is not None:
        indexed_subset = indexed_subset[: args.max_cases]
    subset = [
        (idx, row)
        for idx, row in indexed_subset
        if prompt_result_key(row) not in tested_keys
    ]

    if tested_keys:
        print(
            f"Found {len(tested_keys)} tested prompts in existing summary: {summary_path}")
    print(f"Running {len(subset)} prompt(s) after id/question-based skip.")

    uqs = get_querying_system()
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

    combined_results = dedupe_case_results(existing_cases + results)
    summary = summarize(combined_results)
    summary_safe = eval_to_json_safe(summary)
    print("\nSummary:")
    summary_for_console = {
        k: v
        for k, v in summary_safe.items()
        if k not in {"sql_match_cases", "sql_mismatch_cases"}
    }
    print(json.dumps(summary_for_console, indent=2))

    write_summary(summary_path, combined_results)
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

import json
import math
import re
from collections import Counter
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple

import pandas as pd


def load_rows(path: Path) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            rows.append(json.loads(line))
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
    expr = re.sub(r"\s+as\s+[a-zA-Z_][a-zA-Z0-9_]*$", "", expr, flags=re.IGNORECASE)
    expr = re.sub(
        r"^(.*[\]\)`\"'\w])\s+[a-zA-Z_][a-zA-Z0-9_]*$",
        r"\1",
        expr,
        flags=re.IGNORECASE,
    )
    return expr


def extract_expression_alias(expr: str) -> Optional[str]:
    expr = expr.strip()
    m = re.search(r"\s+as\s+([a-zA-Z_][a-zA-Z0-9_]*)$", expr, flags=re.IGNORECASE)
    if m:
        return m.group(1).lower()

    m = re.search(r"^(.*[\]\)`\"'\w])\s+([a-zA-Z_][a-zA-Z0-9_]*)$", expr, flags=re.IGNORECASE)
    if m and strip_expression_alias(expr) != expr:
        return m.group(2).lower()
    return None


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
    terms = split_top_level_bool(where_clause, "and")
    normalized_terms = [normalize_simple_expression(t) for t in terms if t.strip()]
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


def normalize_cte_names(sql: str) -> str:
    cte_pattern = re.compile(r"\b([a-zA-Z_][a-zA-Z0-9_]*)\s+as\s*\(", flags=re.IGNORECASE)

    matches = list(cte_pattern.finditer(sql))
    if not matches:
        return sql

    cte_map: Dict[str, str] = {}
    next_idx = 1
    for match in matches:
        name = match.group(1).lower()
        if name in {"select", "from", "where", "group", "having", "order", "limit"}:
            continue
        if name not in cte_map:
            cte_map[name] = f"cte_{next_idx}"
            next_idx += 1

    normalized = sql
    for original_name, placeholder in sorted(cte_map.items(), key=lambda item: len(item[0]), reverse=True):
        normalized = re.sub(
            rf"\b{re.escape(original_name)}\b",
            placeholder,
            normalized,
            flags=re.IGNORECASE,
        )
    return normalized


def split_ctes_and_main_query(sql: str) -> Tuple[List[Tuple[str, str]], str]:
    s = sql.strip()
    if not s.lower().startswith("with "):
        return [], s

    ctes: List[Tuple[str, str]] = []
    pos = 4
    length = len(s)

    while pos < length:
        while pos < length and s[pos].isspace():
            pos += 1

        match = re.match(r"([a-zA-Z_][a-zA-Z0-9_]*)\s+as\s*\(", s[pos:], flags=re.IGNORECASE)
        if not match:
            break

        cte_name = match.group(1).lower()
        pos += match.end()
        body_start = pos
        depth = 1
        in_single = False
        in_double = False

        while pos < length and depth > 0:
            ch = s[pos]
            if ch == "'" and not in_double:
                in_single = not in_single
            elif ch == '"' and not in_single:
                in_double = not in_double
            elif not in_single and not in_double:
                if ch == "(":
                    depth += 1
                elif ch == ")":
                    depth -= 1
            pos += 1

        if depth != 0:
            break

        ctes.append((cte_name, s[body_start : pos - 1].strip()))

        while pos < length and s[pos].isspace():
            pos += 1
        if pos >= length or s[pos] != ",":
            break
        pos += 1

    return ctes, s[pos:].strip()


def extract_cte_output_aliases(cte_bodies: List[Tuple[str, str]]) -> Dict[str, Dict[str, str]]:
    cte_aliases: Dict[str, Dict[str, str]] = {}

    for cte_name, body in cte_bodies:
        select_clause = extract_clause(
            body,
            "select",
            ["from", "where", "group by", "having", "order by", "limit"],
        )
        if not select_clause:
            continue

        alias_map: Dict[str, str] = {}
        for idx, item in enumerate(split_top_level(select_clause)):
            alias = extract_expression_alias(item)
            if alias:
                alias_map[alias] = f"__cte_col_{idx}__"

        if alias_map:
            cte_aliases[cte_name] = alias_map

    return cte_aliases


def normalize_cte_column_references(
    expr: str,
    table_alias_map: Dict[str, str],
    cte_output_aliases: Dict[str, Dict[str, str]],
) -> str:
    out = expr
    qualifier_targets: Dict[str, str] = {}

    for cte_name in cte_output_aliases:
        qualifier_targets[cte_name] = cte_name
    for alias, table_name in table_alias_map.items():
        if table_name in cte_output_aliases:
            qualifier_targets[alias] = table_name

    for qualifier, cte_name in sorted(qualifier_targets.items(), key=lambda item: len(item[0]), reverse=True):
        for alias_name, placeholder in cte_output_aliases[cte_name].items():
            out = re.sub(
                rf"\b{re.escape(qualifier)}\.{re.escape(alias_name)}\b",
                f"{cte_name}.{placeholder}",
                out,
                flags=re.IGNORECASE,
            )

    return out


def canonicalize_select_query(
    sql: str,
    cte_output_aliases: Dict[str, Dict[str, str]],
) -> str:
    alias_map = extract_table_alias_map(sql)
    clause_order = ["select", "from", "where", "group by"]
    next_keywords = {
        "select": ["from", "where", "group by", "having", "order by", "limit"],
        "from": ["where", "group by", "having", "order by", "limit"],
        "where": ["group by", "having", "order by", "limit"],
        "group by": ["having", "order by", "limit"],
    }

    canonical_parts: List[str] = []
    for clause in clause_order:
        content = extract_clause(sql, clause, next_keywords[clause])
        if not content:
            continue
        content = normalize_cte_column_references(content, alias_map, cte_output_aliases)
        content = remove_alias_references(content, alias_map)
        if clause in {"select", "group by"}:
            items = [
                normalize_simple_expression(strip_expression_alias(p))
                for p in split_top_level(content)
            ]
            items.sort()
            content = ",".join(items)
        elif clause == "from":
            content = normalize_from_clause(content)
        elif clause == "where":
            content = normalize_where_clause(content)
        else:
            content = normalize_simple_expression(content)
        canonical_parts.append(f"{clause}:{content}")

    if not canonical_parts:
        return normalize_simple_expression(sql)
    return " | ".join(canonical_parts)


def canonicalize_sql(sql: str) -> str:
    s = sql.replace("\\n", " ").replace("\\t", " ").replace("\\r", " ")
    s = s.strip().strip(";")
    s = re.sub(r"\s+", " ", s)
    s_low = normalize_cte_names(s.lower())
    cte_bodies, main_query = split_ctes_and_main_query(s_low)
    cte_output_aliases = extract_cte_output_aliases(cte_bodies)

    canonical_ctes = [
        f"cte:{cte_name}:{canonicalize_select_query(cte_body, cte_output_aliases)}"
        for cte_name, cte_body in cte_bodies
    ]
    canonical_main = canonicalize_select_query(main_query, cte_output_aliases)

    if not canonical_ctes:
        return canonical_main
    return " | ".join(canonical_ctes + [f"main:{canonical_main}"])


def normalize_scalar(value: Any) -> str:
    if pd.isna(value):
        return "__NULL__"
    if isinstance(value, float):
        normalized = f"{value:.10g}"
    else:
        normalized = str(value).strip().lower()

    if normalized in {"yes", "true", "1"}:
        return "__BOOL_TRUE__"
    if normalized in {"no", "false", "0"}:
        return "__BOOL_FALSE__"
    return normalized


def normalize_column_name(name: Any) -> str:
    normalized = re.sub(r"[^a-z0-9]+", " ", str(name).strip().lower())
    return " ".join(normalized.split())


def normalize_dataframe_values(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    out.columns = [str(col) for col in out.columns]

    for col in out.columns:
        out[col] = out[col].map(normalize_scalar)

    return out


def dataframe_to_row_counter(df: pd.DataFrame) -> Tuple[List[str], Counter]:
    columns = list(df.columns)
    rows = Counter(tuple(row) for row in df.itertuples(index=False, name=None))
    return columns, rows


def column_value_counter(series: pd.Series) -> Counter:
    return Counter(series.tolist())


def column_name_similarity(expected_name: Any, generated_name: Any) -> float:
    expected_normalized = normalize_column_name(expected_name)
    generated_normalized = normalize_column_name(generated_name)
    if not expected_normalized and not generated_normalized:
        return 1.0
    if not expected_normalized or not generated_normalized:
        return 0.0
    if expected_normalized == generated_normalized:
        return 1.0

    expected_tokens = " ".join(sorted(expected_normalized.split()))
    generated_tokens = " ".join(sorted(generated_normalized.split()))
    direct_similarity = SequenceMatcher(None, expected_normalized, generated_normalized).ratio()
    token_similarity = SequenceMatcher(None, expected_tokens, generated_tokens).ratio()
    return max(direct_similarity, token_similarity)


def column_content_similarity(expected_series: pd.Series, generated_series: pd.Series) -> float:
    expected_values = column_value_counter(expected_series)
    generated_values = column_value_counter(generated_series)
    expected_total = sum(expected_values.values())
    generated_total = sum(generated_values.values())
    max_total = max(expected_total, generated_total, 1)
    shared_total = sum((expected_values & generated_values).values())
    return shared_total / max_total


def align_dataframe_columns(expected_df: pd.DataFrame, generated_df: pd.DataFrame) -> Tuple[pd.DataFrame, pd.DataFrame, int]:
    expected_normalized = normalize_dataframe_values(expected_df)
    generated_normalized = normalize_dataframe_values(generated_df)

    expected_columns = list(expected_normalized.columns)
    generated_columns = list(generated_normalized.columns)
    if not expected_columns or not generated_columns:
        return expected_normalized.iloc[:, 0:0], generated_normalized.iloc[:, 0:0], 0

    candidate_pairs: List[Tuple[float, float, float, int, int]] = []
    for expected_idx, expected_col in enumerate(expected_columns):
        for generated_idx, generated_col in enumerate(generated_columns):
            name_similarity = column_name_similarity(expected_col, generated_col)
            content_similarity = column_content_similarity(
                expected_normalized[expected_col],
                generated_normalized[generated_col],
            )
            combined_similarity = (0.6 * name_similarity) + (0.4 * content_similarity)
            candidate_pairs.append(
                (combined_similarity, name_similarity, content_similarity, expected_idx, generated_idx)
            )

    candidate_pairs.sort(reverse=True)

    matched_expected: set[int] = set()
    matched_generated: set[int] = set()
    matched_pairs: List[Tuple[int, int]] = []
    for _combined, _name, _content, expected_idx, generated_idx in candidate_pairs:
        if expected_idx in matched_expected or generated_idx in matched_generated:
            continue
        matched_expected.add(expected_idx)
        matched_generated.add(generated_idx)
        matched_pairs.append((expected_idx, generated_idx))
        if len(matched_pairs) == min(len(expected_columns), len(generated_columns)):
            break

    matched_pairs.sort()
    expected_aligned = expected_normalized[[expected_columns[i] for i, _ in matched_pairs]].copy()
    generated_aligned = generated_normalized[[generated_columns[j] for _, j in matched_pairs]].copy()

    shared_columns = [f"col__{idx}" for idx in range(len(matched_pairs))]
    expected_aligned.columns = shared_columns
    generated_aligned.columns = shared_columns
    return expected_aligned, generated_aligned, len(matched_pairs)


def dataframe_match_score(expected_df: pd.DataFrame, generated_df: pd.DataFrame) -> float:
    expected_col_count = len(expected_df.columns)
    generated_col_count = len(generated_df.columns)
    max_col_count = max(expected_col_count, generated_col_count, 1)
    expected_aligned, generated_aligned, matched_col_count = align_dataframe_columns(expected_df, generated_df)
    column_coverage = matched_col_count / max_col_count

    if matched_col_count == 0:
        return 1.0 if len(expected_df) == 0 and len(generated_df) == 0 else 0.0

    _expected_columns, expected_rows = dataframe_to_row_counter(expected_aligned)
    _generated_columns, generated_rows = dataframe_to_row_counter(generated_aligned)

    expected_row_total = sum(expected_rows.values())
    generated_row_total = sum(generated_rows.values())
    if expected_row_total == 0 and generated_row_total == 0:
        return float(column_coverage)

    max_row_count = max(expected_row_total, generated_row_total, 1)
    shared_row_count = sum((expected_rows & generated_rows).values())
    row_overlap = shared_row_count / max_row_count

    return float(column_coverage * row_overlap)


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


def load_generated_df_from_csv(row: Dict[str, Any], engine: Any) -> pd.DataFrame:
    del engine
    generated_df_csv = row.get("generated_df_csv")
    if generated_df_csv:
        return pd.read_csv(generated_df_csv)
    return pd.DataFrame()


def load_generated_df_from_csv_or_sql(row: Dict[str, Any], engine: Any) -> pd.DataFrame:
    generated_df_csv = row.get("generated_df_csv")
    if generated_df_csv:
        return pd.read_csv(generated_df_csv)

    generated_sql = (row.get("generated_sql") or "").strip()
    if generated_sql:
        return pd.read_sql(generated_sql, engine)

    return pd.DataFrame()


def summarize_prompt_rows(
    rows: List[Dict[str, Any]],
    engine: Any,
    load_generated_df: Callable[[Dict[str, Any], Any], pd.DataFrame],
) -> Dict[str, Any]:
    case_details: List[Dict[str, Any]] = []
    tested_cases = 0
    sql_match_count = 0
    df_exact_match_count = 0
    df_match_score_sum = 0.0
    errors = 0

    for index, row in enumerate(rows):
        generated_sql = (row.get("generated_sql") or "").strip()
        gold_sql = (row.get("sql") or "").strip()
        generated_error = row.get("generated_error")

        case_result: Dict[str, Any] = {
            "index": index,
            "question": row.get("question"),
            "gold_sql": gold_sql,
            "generated_sql": generated_sql,
            "generated_error": generated_error,
            "generated_df_csv": row.get("generated_df_csv"),
            "sql_match": False,
            "df_match_score": None,
            "gold_df_preview": {"row_count": None, "preview_rows": []},
            "generated_df_preview": row.get("generated_df_preview") or {"row_count": None, "preview_rows": []},
        }

        if not generated_sql and not generated_error:
            case_result["status"] = "untested"
            case_details.append(case_result)
            continue

        tested_cases += 1
        if generated_error:
            errors += 1
            case_result["status"] = "error"
            case_details.append(case_result)
            continue

        case_result["status"] = "tested"
        sql_match = canonicalize_sql(generated_sql) == canonicalize_sql(gold_sql)
        case_result["sql_match"] = sql_match
        if sql_match:
            sql_match_count += 1

        try:
            expected_df = pd.read_sql(gold_sql, engine)
            case_result["gold_df_preview"] = dataframe_preview(expected_df, max_rows=5)
            generated_df = load_generated_df(row, engine)
            case_result["generated_df_preview"] = dataframe_preview(generated_df, max_rows=5)

            score = 1.0 if sql_match else dataframe_match_score(expected_df, generated_df)
            case_result["df_match_score"] = score
            df_match_score_sum += score
            if score == 1.0:
                df_exact_match_count += 1
        except Exception as exc:
            errors += 1
            case_result["status"] = "error"
            case_result["generated_error"] = str(exc)

        case_details.append(case_result)

    total_cases = len(rows)
    tested_denominator = tested_cases if tested_cases else 1
    return {
        "total_prompts": total_cases,
        "tested_prompts": tested_cases,
        "untested_prompts": total_cases - tested_cases,
        "error_count": errors,
        "sql_match_count": sql_match_count,
        "sql_match_rate_across_tested": sql_match_count / tested_denominator,
        "df_exact_match_count": df_exact_match_count,
        "df_exact_match_rate_across_tested": df_exact_match_count / tested_denominator,
        "average_df_match_score_across_tested": df_match_score_sum / tested_denominator,
        "case_details": case_details,
    }

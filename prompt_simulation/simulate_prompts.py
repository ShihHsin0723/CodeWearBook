import argparse
import gzip
import io
import json
import re
import sys
import warnings
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Set, Tuple

import boto3
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from create_and_load_BUMP_tables import (
    DATA_FILES as BUMP_DATA_FILES,
    bucket_name as BUMP_BUCKET,
)
from create_and_load_SinC_tables import (
    DATA_FILES as SINC_DATA_FILES,
    bucket_name as SINC_BUCKET,
)
from dataset_registry import SCOPE_BUMP, SCOPE_SINC, normalize_scope_name


warnings.filterwarnings(
    "ignore",
    message="Could not infer format, so each element will be parsed individually*",
    category=UserWarning,
)


SCRIPT_DIR = Path(__file__).resolve().parent
PROMPTS_DIR = PROJECT_ROOT / "prompts"
DEFAULT_LIMIT_N = 20
DEFAULT_SAMPLE_ROWS = 100
DEFAULT_SAMPLE_K = 2
DEFAULT_MODE = "w"

BUMP_CURATED_TABLES = [
    "bump_quick_activities",
    "bump_surveys",
    "bump_bodyport",
    "bump_ebt",
    "bump_nbx",
    "bump_pvt",
    "bump_garmin_dailies",
    "bump_garmin_epochs",
    "bump_garmin_pulse_ox",
    "bump_garmin_respiration",
    "bump_garmin_sleep",
    "bump_garmin_stress_details",
    "bump_oura_activity",
    "bump_oura_readiness",
    "bump_oura_sleep",
    "bump_ace",
    "bump_adverse_event_report",
    "bump_birthing_data",
    "bump_cssrs",
    "bump_phq9",
    "bump_gad7",
]

DATASET_SOURCES = {
    SCOPE_BUMP: {
        "bucket": BUMP_BUCKET,
        "data_files": BUMP_DATA_FILES,
        "curated_tables": BUMP_CURATED_TABLES,
    },
    SCOPE_SINC: {
        "bucket": SINC_BUCKET,
        "data_files": SINC_DATA_FILES,
        "curated_tables": [],
    },
}

s3 = boto3.client("s3")


def normalize_dataset_name(dataset: str) -> str:
    scope = normalize_scope_name(dataset)
    if scope not in {SCOPE_BUMP, SCOPE_SINC}:
        raise ValueError(
            f"Unsupported dataset for prompt simulation: {dataset}. "
            "Use BUMP or SinC."
        )
    return scope


def dataset_slug(scope: str) -> str:
    return scope.lower().replace("-", "_")


def default_output_paths(scope: str) -> Tuple[Path, Path]:
    slug = dataset_slug(scope)
    return (
        SCRIPT_DIR / slug / "schema_index.jsonl",
        PROMPTS_DIR / "simulated" / f"{slug}_prompts.jsonl",
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Generate schema snapshots and synthetic text-to-SQL prompts for "
            "BUMP or SinC source files."
        )
    )
    parser.add_argument(
        "--dataset",
        default="bump",
        help="Dataset to simulate against: bump or sinc.",
    )
    parser.add_argument(
        "--include",
        nargs="+",
        help=(
            "Optional table names or S3 keys to include. "
            "Examples: sinc_surveys, survey_monkey/phq_8.csv.gz, "
            "bump/app_activities/surveys.csv.gz."
        ),
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="Process all tables for the selected dataset. This is the default unless --curated is used.",
    )
    parser.add_argument(
        "--curated",
        action="store_true",
        help=(
            "Use the curated table subset for datasets that define one. "
            "Currently this only affects BUMP."
        ),
    )
    parser.add_argument(
        "--schema-out",
        type=Path,
        help="Path to write schema metadata JSONL.",
    )
    parser.add_argument(
        "--test-out",
        type=Path,
        help="Path to write generated text-to-SQL prompts JSONL.",
    )
    parser.add_argument(
        "--mode",
        choices=["w", "a"],
        default=DEFAULT_MODE,
        help="File write mode for the output JSONL files.",
    )
    parser.add_argument(
        "--limit-n",
        type=int,
        default=DEFAULT_LIMIT_N,
        help="LIMIT value used when generating SQL examples.",
    )
    parser.add_argument(
        "--sample-rows",
        type=int,
        default=DEFAULT_SAMPLE_ROWS,
        help="Number of source rows to inspect per file.",
    )
    parser.add_argument(
        "--sample-k",
        type=int,
        default=DEFAULT_SAMPLE_K,
        help="Number of example values to retain per column.",
    )
    return parser.parse_args()


def resolve_requested_table(
    item: str,
    data_files: Dict[str, str],
) -> str:
    cleaned = item.strip().lstrip("/")
    if cleaned in data_files:
        return cleaned

    direct_matches = [table for table, key in data_files.items() if key == cleaned]
    if len(direct_matches) == 1:
        return direct_matches[0]

    suffix_matches = [table for table, key in data_files.items() if key.endswith(cleaned)]
    if len(suffix_matches) == 1:
        return suffix_matches[0]

    raise ValueError(f"Could not match selection '{item}' to a known table or S3 key.")


def select_tables(
    scope: str,
    requested_items: Optional[Iterable[str]],
    use_all: bool,
) -> List[str]:
    config = DATASET_SOURCES[scope]
    data_files = config["data_files"]
    curated_tables = config["curated_tables"]

    if requested_items:
        requested_tables = {
            resolve_requested_table(item, data_files)
            for item in requested_items
        }
        return [table for table in data_files if table in requested_tables]

    if not use_all and curated_tables:
        return [table for table in data_files if table in set(curated_tables)]

    return list(data_files.keys())


def read_head_df(bucket: str, key: str, sample_rows: int) -> pd.DataFrame:
    body = s3.get_object(Bucket=bucket, Key=key)["Body"].read()
    if key.endswith(".gz"):
        reader = gzip.GzipFile(fileobj=io.BytesIO(body))
    else:
        reader = io.BytesIO(body)
    return pd.read_csv(reader, nrows=sample_rows)


def infer_type(series: pd.Series) -> str:
    non_null = series.dropna()
    if non_null.empty:
        return "unknown"
    if pd.api.types.is_integer_dtype(series):
        return "integer"
    if pd.api.types.is_float_dtype(series):
        return "float"
    if series.dtype == "object":
        if (
            pd.to_datetime(non_null.astype(str), errors="coerce", utc=True)
            .notna()
            .mean()
            >= 0.8
        ):
            return "datetime"
    return "text"


def sample_vals(series: pd.Series, sample_k: int) -> List[object]:
    out: List[object] = []
    for value in series.dropna().head(500):
        normalized = value.item() if hasattr(value, "item") else value
        if normalized not in out:
            out.append(normalized)
        if len(out) >= sample_k:
            break
    return out


def pick(cols: Iterable[str], want: Iterable[str]) -> Optional[str]:
    for col in cols:
        lowered = col.lower()
        if any(candidate in lowered for candidate in want):
            return col
    return None


def lit(value: object, inferred_type: str) -> str:
    if inferred_type in {"integer", "float"}:
        return str(value)
    return "'" + str(value).replace("'", "''") + "'"


def date_lit(value: object) -> str:
    parsed = pd.to_datetime(value, errors="coerce")
    if pd.notna(parsed):
        return f"'{parsed.date().isoformat()}'"
    return lit(value, "text")


SQL_RESERVED_WORDS = {
    "group",
    "from",
    "to",
    "order",
    "limit",
    "where",
    "select",
    "table",
    "index",
}


def sql_ident(name: str) -> str:
    if re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", name) and name.lower() not in SQL_RESERVED_WORDS:
        return name
    return '"' + name.replace('"', '""') + '"'


def display_table_name(table: str, scope: str) -> str:
    prefix = f"{dataset_slug(scope)}_"
    base_name = table[len(prefix):] if table.startswith(prefix) else table
    return f"{scope} {base_name.replace('_', ' ')}"


def write_tests(
    tf,
    table: str,
    cols: List[str],
    types: Dict[str, str],
    ex: Dict[str, List[object]],
    entity: Optional[str],
    scope: str,
    limit_n: int,
) -> None:
    tid = 0
    table_label = display_table_name(table, scope)

    def add(question: str, sql: str, tags: List[str]) -> None:
        nonlocal tid
        tf.write(
            json.dumps(
                {
                    "id": f"{table}::q_{tid:03d}",
                    "dataset": scope,
                    "table": table,
                    "schema": cols,
                    "question": question,
                    "sql": sql,
                    "tags": tags,
                },
                ensure_ascii=False,
            )
            + "\n"
        )
        tid += 1

    def with_table_context(question: str) -> str:
        q = question.rstrip()
        if q.endswith("?"):
            q = q[:-1]
        return f"{q} in the {table_label} table?"

    def fmt(col: str) -> str:
        return str(col).replace("_", " ").replace(".", " ")

    def first_sample(col: Optional[str]) -> Optional[object]:
        return (ex.get(col) or [None])[0] if col else None

    def first_col_by_type(
        want_types: Set[str],
        exclude: Optional[Set[str]] = None,
    ) -> Optional[str]:
        excluded = exclude or set()
        for col in cols:
            if col in excluded:
                continue
            if types.get(col) in want_types:
                return col
        return None

    def first_textish_col(exclude: Optional[Set[str]] = None) -> Optional[str]:
        excluded = exclude or set()
        for col in cols:
            if col in excluded:
                continue
            if types.get(col) in {"text", "unknown"} and first_sample(col) is not None:
                return col
        return None

    c0 = entity or cols[0]
    add(
        f"What are {limit_n} users in the {table_label} data?"
        if entity and c0.lower() == "user_id"
        else with_table_context(f"What are {limit_n} distinct {fmt(c0)} values"),
        f"SELECT DISTINCT {sql_ident(c0)} FROM {sql_ident(table)} LIMIT {limit_n};",
        ["distinct", "limit"],
    )

    n = len(cols)
    for i in range(n):
        used = [cols[i], cols[(i + 1) % n]] + ([] if n == 2 else [cols[(i + 2) % n]])
        display_names = ", ".join(fmt(col) for col in used)
        add(
            with_table_context(f"Show {limit_n} rows of {display_names}"),
            f"SELECT {', '.join(sql_ident(col) for col in used)} "
            f"FROM {sql_ident(table)} LIMIT {limit_n};",
            ["select", "limit", "multi_col"],
        )

    add(
        with_table_context("How many rows are there"),
        f"SELECT COUNT(*) FROM {sql_ident(table)};",
        ["count"],
    )

    where_col = entity or cols[0]
    sample_value = first_sample(where_col)
    if sample_value is not None:
        add(
            with_table_context(f"For {fmt(where_col)} = {sample_value}, show {limit_n} rows"),
            f"SELECT * FROM {sql_ident(table)} WHERE {sql_ident(where_col)} = {lit(sample_value, types[where_col])} "
            f"LIMIT {limit_n};",
            ["where", "eq", "limit"],
        )

    text_col = first_textish_col(exclude={where_col})
    text_value = first_sample(text_col)
    if text_col and text_value is not None:
        add(
            with_table_context(f"For {fmt(text_col)} = {text_value}, show {limit_n} rows"),
            f"SELECT * FROM {sql_ident(table)} WHERE {sql_ident(text_col)} = {lit(text_value, types[text_col])} "
            f"LIMIT {limit_n};",
            ["where", "eq", "text", "limit"],
        )

    datetime_col = first_col_by_type({"datetime"})
    datetime_value = first_sample(datetime_col)
    if datetime_col and datetime_value is not None:
        add(
            with_table_context(
                f"Show {limit_n} rows where {fmt(datetime_col)} is on or after {datetime_value}"
            ),
            f"SELECT * FROM {sql_ident(table)} WHERE DATE({sql_ident(datetime_col)}) >= {date_lit(datetime_value)} "
            f"LIMIT {limit_n};",
            ["where", "range", "datetime", "limit"],
        )

    numeric_col = first_col_by_type(
        {"integer", "float"},
        exclude={col for col in cols if "id" in col.lower()},
    ) or first_col_by_type({"integer", "float"})
    numeric_value = first_sample(numeric_col)
    if numeric_col and numeric_value is not None:
        add(
            with_table_context(
                f"Show {limit_n} rows where {fmt(numeric_col)} is greater than {numeric_value}"
            ),
            f"SELECT * FROM {sql_ident(table)} WHERE {sql_ident(numeric_col)} > {lit(numeric_value, types[numeric_col])} "
            f"LIMIT {limit_n};",
            ["where", "gt", "numeric", "limit"],
        )

    if entity:
        entity_value = first_sample(entity)
        if entity_value is not None and text_col and text_value is not None:
            add(
                with_table_context(
                    f"For {fmt(entity)} = {entity_value} and {fmt(text_col)} = {text_value}, show {limit_n} rows"
                ),
                f"SELECT * FROM {sql_ident(table)} WHERE {sql_ident(entity)} = {lit(entity_value, types[entity])} "
                f"AND {sql_ident(text_col)} = {lit(text_value, types[text_col])} "
                f"LIMIT {limit_n};",
                ["where", "and", "eq", "limit"],
            )

    if datetime_col and datetime_value is not None and numeric_col and numeric_value is not None:
        add(
            with_table_context(
                f"Show {limit_n} rows where {fmt(datetime_col)} is on or after {datetime_value} "
                f"and {fmt(numeric_col)} is greater than {numeric_value}"
            ),
            f"SELECT * FROM {sql_ident(table)} WHERE DATE({sql_ident(datetime_col)}) >= {date_lit(datetime_value)} "
            f"AND {sql_ident(numeric_col)} > {lit(numeric_value, types[numeric_col])} "
            f"LIMIT {limit_n};",
            ["where", "and", "range", "numeric", "limit"],
        )

    if entity:
        group_entity = "record_id" if "record_id" in cols else entity
        group_label = "users" if group_entity.lower() == "user_id" else f"{fmt(group_entity)} values"
        add(
            with_table_context(f"Which 5 {group_label} have the most records"),
            f"SELECT {sql_ident(group_entity)}, COUNT(*) AS n FROM {sql_ident(table)} "
            f"GROUP BY {sql_ident(group_entity)} ORDER BY n DESC LIMIT 5;",
            ["group_by", "count", "limit"],
        )


def main() -> None:
    args = parse_args()
    scope = normalize_dataset_name(args.dataset)
    bucket = DATASET_SOURCES[scope]["bucket"]
    data_files = DATASET_SOURCES[scope]["data_files"]
    selected_tables = select_tables(scope, args.include, args.all or not args.curated)

    schema_out, test_out = default_output_paths(scope)
    if args.schema_out is not None:
        schema_out = args.schema_out
    if args.test_out is not None:
        test_out = args.test_out

    schema_out.parent.mkdir(parents=True, exist_ok=True)
    test_out.parent.mkdir(parents=True, exist_ok=True)

    print(f"Starting Text-to-SQL test generation for {scope}...")
    print(f"Selected {len(selected_tables)} table(s) for processing")

    with schema_out.open(args.mode, encoding="utf-8") as sf, test_out.open(
        args.mode, encoding="utf-8"
    ) as tf:
        for idx, table in enumerate(selected_tables, 1):
            key = data_files[table]
            print(f"\n[{idx}/{len(selected_tables)}] Processing table: {table}")
            print(f"  → Source: s3://{bucket}/{key}")

            df = read_head_df(bucket, key, args.sample_rows)
            print(f"  → Loaded {len(df)} rows, {len(df.columns)} columns")

            cols = list(df.columns)
            types = {col: infer_type(df[col]) for col in cols}
            examples = {col: sample_vals(df[col], args.sample_k) for col in cols}
            entity = pick(cols, ["user_id", "record_id"])

            print(f"  → Entity column: {entity if entity else 'None'}")

            sf.write(
                json.dumps(
                    {
                        "dataset": scope,
                        "s3_bucket": bucket,
                        "s3_key": key,
                        "sql_table": table,
                        "columns": cols,
                        "column_types": types,
                        "example_values": examples,
                    },
                    ensure_ascii=False,
                )
                + "\n"
            )

            write_tests(tf, table, cols, types, examples, entity, scope, args.limit_n)
            print(f"✓ Done generating questions for: {table}")

    print("\nAll files processed successfully.")
    print("Wrote:", schema_out, "and", test_out)


if __name__ == "__main__":
    main()

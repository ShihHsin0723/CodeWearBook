"""Local deterministic loader for manifest-defined CSV and Parquet tables.

This module never imports or calls an LLM. It logs only structural metadata and
row counts; source values remain in local process memory and the target DB.
"""

import io
import re
from pathlib import Path
from typing import Any, Dict, Iterable, Tuple
from urllib.parse import urlparse

import boto3
import pandas as pd
from sqlalchemy import Boolean, Column, Date, DateTime, Float, Integer, MetaData, Table, Text, text


PROJECT_ROOT = Path(__file__).resolve().parent
TYPE_MAP = {
    "text": Text,
    "integer": Integer,
    "float": Float,
    "boolean": Boolean,
    "date": Date,
    "datetime": DateTime,
}
BOOLEAN_TRUE = {"true", "t", "yes", "y", "1"}
BOOLEAN_FALSE = {"false", "f", "no", "n", "0"}


def normalize_column_name(name: str) -> str:
    normalized = re.sub(r"[^a-zA-Z0-9_]+", "_", str(name).strip()).strip("_").lower()
    if not normalized:
        raise ValueError(f"Column name cannot be normalized safely: {name!r}")
    if normalized[0].isdigit():
        normalized = f"column_{normalized}"
    return normalized


def _source_handle(source: Dict[str, Any], *, stream: bool = False):
    uri = str(source["uri"])
    if uri.startswith("s3://"):
        parsed = urlparse(uri)
        response = boto3.client("s3").get_object(
            Bucket=parsed.netloc,
            Key=parsed.path.lstrip("/"),
        )
        body = response["Body"]
        if stream:
            return body
        try:
            return io.BytesIO(body.read())
        finally:
            body.close()
    path = Path(uri).expanduser()
    if not path.is_absolute():
        path = PROJECT_ROOT / path
    if not path.is_file():
        raise FileNotFoundError(f"Dataset source does not exist: {path}")
    return path


def _csv_options(source: Dict[str, Any]):
    options = {
        "sep": source.get("delimiter", ","),
        "encoding": source.get("encoding", "utf-8"),
        "compression": source.get("compression", "infer"),
        "dtype": str,
        "low_memory": False,
    }
    if options["compression"] == "infer" and str(source["uri"]).lower().split("?", 1)[0].endswith(".gz"):
        options["compression"] = "gzip"
    return options


def _read_source(table_config: Dict[str, Any], *, sample_rows=None):
    source = table_config["source"]
    source_format = str(source.get("format", "csv")).lower()
    if source_format == "parquet":
        handle = _source_handle(source)
        try:
            frame = pd.read_parquet(handle, columns=source.get("columns"))
            return frame.head(sample_rows) if sample_rows else frame
        finally:
            if hasattr(handle, "close"):
                handle.close()

    options = _csv_options(source)
    if sample_rows:
        options["nrows"] = sample_rows
    handle = _source_handle(source, stream=str(source["uri"]).startswith("s3://"))
    try:
        return pd.read_csv(handle, **options)
    finally:
        if hasattr(handle, "close"):
            handle.close()


def _iter_source_frames(table_config: Dict[str, Any]):
    source = table_config["source"]
    if str(source.get("format", "csv")).lower() == "parquet":
        yield _read_source(table_config)
        return
    options = _csv_options(source)
    options["chunksize"] = int(source.get("chunksize", 50000))
    handle = _source_handle(source, stream=str(source["uri"]).startswith("s3://"))
    try:
        yield from pd.read_csv(handle, **options)
    finally:
        if hasattr(handle, "close"):
            handle.close()


def _infer_type(series: pd.Series, column_name: str, entity_key: str, date_columns) -> str:
    if column_name == entity_key or column_name.endswith("_id"):
        return "text"
    non_null = series.dropna()
    if non_null.empty:
        return "text"
    lowered = non_null.astype(str).str.strip().str.lower()
    boolean_values = BOOLEAN_TRUE | BOOLEAN_FALSE
    if lowered.isin(boolean_values).all():
        return "boolean"
    numeric = pd.to_numeric(non_null, errors="coerce")
    if numeric.notna().all():
        return "integer" if (numeric % 1 == 0).all() else "float"
    date_hint = column_name in date_columns or any(
        token in column_name for token in ("date", "time", "timestamp")
    )
    if date_hint:
        parsed = pd.to_datetime(non_null, errors="coerce")
        if parsed.notna().all():
            return "date" if "date" in column_name and "time" not in column_name else "datetime"
    return "text"


def _prepare_columns(frame: pd.DataFrame, table_config: Dict[str, Any], entity_key: str):
    normalized_sources = {}
    for source_name in frame.columns:
        normalized = normalize_column_name(source_name)
        if normalized in normalized_sources:
            raise ValueError(f"Multiple source columns normalize to {normalized!r}")
        normalized_sources[normalized] = source_name

    configured = table_config.get("columns") or {}
    infer_unlisted = bool(table_config.get("infer_unlisted_columns", True))
    output = pd.DataFrame(index=frame.index)
    settings_by_target = {}
    consumed_sources = set()

    for target, settings in configured.items():
        source = str(settings.get("source", target))
        actual_source = source if source in frame.columns else normalized_sources.get(normalize_column_name(source))
        if actual_source is None:
            if settings.get("required", False):
                raise ValueError(f"Required source column {source!r} is missing")
            output[target] = None
        else:
            output[target] = frame[actual_source]
            consumed_sources.add(actual_source)
        settings_by_target[target] = settings

    if infer_unlisted:
        for normalized, actual_source in normalized_sources.items():
            if actual_source not in consumed_sources and normalized not in output.columns:
                output[normalized] = frame[actual_source]
                settings_by_target[normalized] = {}

    date_columns = set(table_config.get("date_columns") or ())
    types = {}
    for target in output.columns:
        configured_type = str(settings_by_target[target].get("type", "infer")).lower()
        types[target] = (
            _infer_type(output[target], target, entity_key, date_columns)
            if configured_type == "infer" else configured_type
        )
    return output, types, settings_by_target


def _coerce_frame(frame, types, settings_by_target):
    result = frame.copy()
    for column, column_type in types.items():
        series = result[column]
        if column_type == "text":
            result[column] = series.where(series.notna(), None).map(
                lambda value: str(value) if value is not None else None
            )
        elif column_type in {"integer", "float"}:
            result[column] = pd.to_numeric(series, errors="coerce")
            if column_type == "integer":
                result[column] = result[column].astype("Int64")
        elif column_type == "boolean":
            lowered = series.astype(str).str.strip().str.lower()
            result[column] = lowered.map(
                lambda value: True if value in BOOLEAN_TRUE else False if value in BOOLEAN_FALSE else None
            )
        elif column_type in {"date", "datetime"}:
            result[column] = pd.to_datetime(series, errors="coerce")
            if column_type == "date":
                result[column] = result[column].dt.date

        if settings_by_target[column].get("required", False):
            missing_count = int(result[column].isna().sum())
            if missing_count:
                raise ValueError(
                    f"Required column {column!r} has {missing_count} missing or invalid rows"
                )
    return result


def inspect_table_schema(table_config: Dict[str, Any], entity_key: str, sample_rows=1000):
    """Return column/type metadata only. No source values are returned."""
    sample = _read_source(table_config, sample_rows=sample_rows)
    prepared, types, _ = _prepare_columns(sample, table_config, entity_key)
    return {column: types[column] for column in prepared.columns}


def create_tables(engine, plugin) -> None:
    metadata = MetaData()
    for table_config in plugin.tables:
        table_name = str(table_config["name"])
        column_types = inspect_table_schema(table_config, plugin.entity_key)
        columns = [
            Column(column, TYPE_MAP[column_type], nullable=True)
            for column, column_type in column_types.items()
        ]
        with engine.begin() as connection:
            connection.execute(text(f'DROP TABLE IF EXISTS "{table_name}"'))
        Table(table_name, metadata, *columns)
    metadata.create_all(engine)


def load_data(engine, plugin) -> None:
    for table_config in plugin.tables:
        table_name = str(table_config["name"])
        declared_types = inspect_table_schema(table_config, plugin.entity_key)
        loaded_row_count = 0
        column_count = len(declared_types)
        for frame in _iter_source_frames(table_config):
            prepared, _, settings = _prepare_columns(
                frame, table_config, plugin.entity_key
            )
            loaded = _coerce_frame(prepared, declared_types, settings)
            loaded.to_sql(table_name, engine, if_exists="append", index=False)
            loaded_row_count += len(loaded)
        print(f"Loaded {loaded_row_count} rows into {table_name} ({column_count} columns).")

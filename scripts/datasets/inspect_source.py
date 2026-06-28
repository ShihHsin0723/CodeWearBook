#!/usr/bin/env python3
"""Generate a metadata-only dataset manifest from CSV/Parquet headers and types."""

import argparse
import sys
from pathlib import Path

import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from generic_dataset_loader import (  # noqa: E402
    _prepare_columns,
    _read_source,
    normalize_column_name,
)


def _dataset_prefix(name: str) -> str:
    return normalize_column_name(name) + "_"


def _parse_source(value: str):
    if "=" not in value:
        raise argparse.ArgumentTypeError("Use TABLE_SUFFIX=URI")
    suffix, uri = value.split("=", 1)
    suffix = normalize_column_name(suffix)
    if not uri.strip():
        raise argparse.ArgumentTypeError("Source URI cannot be empty")
    return suffix, uri.strip()


def _infer_format(uri: str) -> str:
    path = uri.lower().split("?", 1)[0]
    return "parquet" if path.endswith((".parquet", ".pq")) else "csv"


def _read_sample(table_config, sample_rows):
    """Read a bounded source sample through the generic loader."""
    return _read_source(table_config, sample_rows=sample_rows)


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Inspect sources locally and generate a manifest containing only "
            "column names and inferred types. Source values are never emitted."
        )
    )
    parser.add_argument("--dataset", required=True)
    parser.add_argument("--entity-key", required=True)
    parser.add_argument(
        "--entity-source",
        help=(
            "Exact source column containing the participant identifier. Required "
            "when its normalized name differs from --entity-key."
        ),
    )
    parser.add_argument(
        "--source",
        action="append",
        type=_parse_source,
        required=True,
        metavar="TABLE_SUFFIX=URI",
    )
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--sample-rows", type=int, default=1000)
    args = parser.parse_args()

    prefix = _dataset_prefix(args.dataset)
    entity_key = normalize_column_name(args.entity_key)
    tables = []
    schema_metadata = {}
    for suffix, uri in args.source:
        table_name = prefix + suffix
        table_config = {
            "name": table_name,
            "source": {"uri": uri, "format": _infer_format(uri)},
            "infer_unlisted_columns": True,
        }
        frame = _read_sample(table_config, args.sample_rows)
        source_columns_by_normalized = {
            normalize_column_name(column): str(column) for column in frame.columns
        }
        entity_source = args.entity_source or source_columns_by_normalized.get(entity_key)
        if entity_source not in frame.columns:
            raise ValueError(
                f"Entity source column was not found for {table_name}. "
                "Provide --entity-source with an exact source column name."
            )
        _, inferred, _ = _prepare_columns(frame, table_config, entity_key)
        columns = {}
        for source_column in frame.columns:
            normalized_source = normalize_column_name(source_column)
            target = entity_key if source_column == entity_source else normalized_source
            columns[target] = {
                "source": str(source_column),
                "type": "text" if target == entity_key else inferred[normalized_source],
            }
        table_config["columns"] = columns
        table_config["infer_unlisted_columns"] = False
        tables.append(table_config)
        schema_metadata[table_name] = {
            "description": f"TODO: describe {table_name}.",
            "keywords": [],
        }

    manifest = {
        "name": args.dataset,
        "aliases": [normalize_column_name(args.dataset)],
        "table_prefix": prefix,
        "entity_key": entity_key,
        "include_in_cross_dataset": True,
        "loader": {"type": "tabular"},
        "tables": tables,
        "sql_guideline": "TODO: describe participant joins and date conventions.",
        "schema_metadata": schema_metadata,
        "kb_documents": [],
        "harmonized_views": {},
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", encoding="utf-8") as handle:
        yaml.safe_dump(manifest, handle, sort_keys=False, allow_unicode=True)
    print(
        f"Wrote metadata-only manifest for {len(tables)} table(s) to {args.output}."
    )
    print("No source row values were written to the manifest or sent to an LLM.")


if __name__ == "__main__":
    main()

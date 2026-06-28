"""Declarative dataset plugin discovery and validation.

Dataset manifests live in ``datasets/registered/*.yaml``.  A manifest contains
the dataset-specific semantics; the query system consumes the resulting
``DatasetPlugin`` objects without dataset-name branches.
"""

from dataclasses import dataclass, field
from importlib import import_module
from pathlib import Path
import re
from typing import Any, Callable, Dict, Iterable, List, Tuple

import yaml


PROJECT_ROOT = Path(__file__).resolve().parent
REGISTERED_DATASET_DIR = PROJECT_ROOT / "datasets" / "registered"
SUPPORTED_HARMONIZED_VIEWS = {
    "wearable_sleep_daily",
    "wearable_activity_daily",
    "wearable_readiness_daily",
    "wearable_bodyport_daily",
    "wearable_ebt_sessions",
    "wearable_ema_daily",
}
CANONICAL_VIEW_COLUMNS = {
    "wearable_sleep_daily": (
        "dataset", "participant_key", "raw_participant_id", "event_date",
        "source_table", "sleep_score", "total_sleep_duration", "hr_average",
        "hr_lowest", "rmssd", "sleep_efficiency",
    ),
    "wearable_activity_daily": (
        "dataset", "participant_key", "raw_participant_id", "event_date",
        "source_table", "activity_score", "steps", "active_calories",
        "total_calories", "average_met",
    ),
    "wearable_readiness_daily": (
        "dataset", "participant_key", "raw_participant_id", "event_date",
        "source_table", "readiness_score", "score_hrv_balance",
        "score_recovery_index", "score_resting_hr",
    ),
    "wearable_bodyport_daily": (
        "dataset", "participant_key", "raw_participant_id", "event_date",
        "source_table", "weight_kg", "bmi_kg_m2", "heart_rate",
        "peripheral_fluid", "sway_velocity_mm_sec", "backend_sway_area_mm2",
    ),
    "wearable_ebt_sessions": (
        "dataset", "participant_key", "raw_participant_id", "event_date",
        "source_table", "ebt_bias_point", "median_rt_total", "mean_rt_total",
        "happy_response_count", "sad_response_count",
    ),
    "wearable_ema_daily": (
        "dataset", "participant_key", "raw_participant_id", "event_date",
        "source_table", "measure", "item_score", "normalized_score",
    ),
}


def _resolve_callable(dotted_path: str) -> Callable:
    if not dotted_path or "." not in dotted_path:
        raise ValueError(f"Invalid Python entrypoint: {dotted_path!r}")
    module_name, function_name = dotted_path.rsplit(".", 1)
    function = getattr(import_module(module_name), function_name, None)
    if not callable(function):
        raise ValueError(f"Entrypoint is not callable: {dotted_path}")
    return function


@dataclass(frozen=True)
class DatasetPlugin:
    name: str
    aliases: Tuple[str, ...]
    table_prefix: str
    entity_key: str
    create_tables_entrypoint: str = ""
    load_data_entrypoint: str = ""
    loader_type: str = "python"
    tables: Tuple[Dict[str, Any], ...] = ()
    include_in_cross_dataset: bool = True
    schema_metadata: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    kb_documents: Tuple[Dict[str, Any], ...] = ()
    harmonized_views: Dict[str, str] = field(default_factory=dict)
    sql_guideline: str = ""
    manifest_path: str = ""

    @property
    def create_tables_fn(self) -> Callable:
        if self.loader_type == "tabular":
            from generic_dataset_loader import create_tables
            return lambda engine: create_tables(engine, self)
        return _resolve_callable(self.create_tables_entrypoint)

    @property
    def load_all_data_fn(self) -> Callable:
        if self.loader_type == "tabular":
            from generic_dataset_loader import load_data
            return lambda engine: load_data(engine, self)
        return _resolve_callable(self.load_data_entrypoint)


def _validate_identifier(value: str, field_name: str) -> None:
    if not value or not value.replace("_", "").isalnum() or value[0].isdigit():
        raise ValueError(f"{field_name} must be a SQL-safe identifier: {value!r}")


def plugin_from_mapping(raw: Dict[str, Any], manifest_path: str = "") -> DatasetPlugin:
    required = {"name", "table_prefix", "entity_key", "loader"}
    missing = sorted(required - set(raw))
    if missing:
        raise ValueError(f"Missing required manifest fields: {', '.join(missing)}")

    loader = raw.get("loader") or {}
    loader_type = str(loader.get("type", "python")).strip().lower()
    if loader_type not in {"python", "tabular"}:
        raise ValueError("loader.type must be 'python' or 'tabular'")
    if loader_type == "python" and (
        not loader.get("create_tables") or not loader.get("load_data")
    ):
        raise ValueError(
            "Python loaders require loader.create_tables and loader.load_data"
        )

    name = str(raw["name"]).strip()
    table_prefix = str(raw["table_prefix"]).strip().lower()
    entity_key = str(raw["entity_key"]).strip()
    _validate_identifier(table_prefix.rstrip("_"), "table_prefix")
    _validate_identifier(entity_key, "entity_key")
    if not table_prefix.endswith("_"):
        raise ValueError("table_prefix must end with an underscore")

    tables = tuple(raw.get("tables") or ())
    if loader_type == "tabular" and not tables:
        raise ValueError("Tabular loaders require at least one tables entry")
    for index, table in enumerate(tables):
        if not isinstance(table, dict) or not table.get("name"):
            raise ValueError(f"tables[{index}] requires a name")
        table_name = str(table["name"])
        _validate_identifier(table_name, f"tables[{index}].name")
        if not table_name.startswith(table_prefix):
            raise ValueError(
                f"Table {table_name!r} must start with {table_prefix!r}"
            )
        source = table.get("source") or {}
        if not source.get("uri"):
            raise ValueError(f"tables[{index}].source.uri is required")
        source_format = str(source.get("format", "csv")).lower()
        if source_format not in {"csv", "parquet"}:
            raise ValueError(f"Unsupported source format: {source_format}")
        columns = table.get("columns") or {}
        if not table.get("infer_unlisted_columns", True) and entity_key not in columns:
            raise ValueError(
                f"{table_name} must configure entity key {entity_key!r} when "
                "infer_unlisted_columns is false"
            )
        for target_name, settings in columns.items():
            _validate_identifier(target_name, f"{table_name} target column")
            if not isinstance(settings, dict):
                raise ValueError(f"Column settings for {target_name} must be a mapping")
            column_type = str(settings.get("type", "infer")).lower()
            if column_type not in {"infer", "text", "integer", "float", "boolean", "date", "datetime"}:
                raise ValueError(f"Unsupported type {column_type!r} for {target_name}")

    schema_metadata = raw.get("schema_metadata") or {}
    for table_name, metadata in schema_metadata.items():
        _validate_identifier(table_name, "schema_metadata table name")
        if not table_name.startswith(table_prefix):
            raise ValueError(
                f"Schema table {table_name!r} must start with {table_prefix!r}"
            )
        if not isinstance(metadata, dict):
            raise ValueError(f"Schema metadata for {table_name} must be a mapping")
        table_keywords = metadata.get("keywords", [])
        if not isinstance(table_keywords, list) or not all(
            isinstance(value, str) for value in table_keywords
        ):
            raise ValueError(f"Schema keywords for {table_name} must be a list of strings")
        column_metadata = metadata.get("columns", {})
        if not isinstance(column_metadata, dict):
            raise ValueError(f"Column metadata for {table_name} must be a mapping")
        for column_name, column_info in column_metadata.items():
            _validate_identifier(column_name, f"{table_name} metadata column")
            if not isinstance(column_info, dict):
                raise ValueError(
                    f"Column metadata for {table_name}.{column_name} must be a mapping"
                )
            for list_field in ("keywords", "aliases"):
                values = column_info.get(list_field, [])
                if not isinstance(values, list) or not all(
                    isinstance(value, str) for value in values
                ):
                    raise ValueError(
                        f"{table_name}.{column_name}.{list_field} must be a list of strings"
                    )

    harmonized_views = raw.get("harmonized_views") or {}
    unsupported = sorted(set(harmonized_views) - SUPPORTED_HARMONIZED_VIEWS)
    if unsupported:
        raise ValueError(f"Unsupported harmonized views: {', '.join(unsupported)}")
    for view_name, select_sql in harmonized_views.items():
        normalized = str(select_sql).strip().rstrip(";")
        if not normalized.lower().startswith("select"):
            raise ValueError(f"{view_name} mapping must be a SELECT statement")
        if ";" in normalized or "--" in normalized or "/*" in normalized:
            raise ValueError(
                f"{view_name} mapping must contain one SELECT without comments"
            )
        raw_table_pattern = rf"\b(?:from|join)\s+{re.escape(table_prefix)}[a-z0-9_]+\b"
        if not re.search(raw_table_pattern, normalized.lower()):
            raise ValueError(
                f"{view_name} mapping must read from a {table_prefix} raw table"
            )
        missing_columns = [
            column for column in CANONICAL_VIEW_COLUMNS[view_name]
            if not re.search(rf"\bas\s+{re.escape(column)}\b", normalized, re.IGNORECASE)
        ]
        if missing_columns:
            raise ValueError(
                f"{view_name} mapping is missing canonical aliases: "
                + ", ".join(missing_columns)
            )
        harmonized_views[view_name] = normalized

    aliases = tuple(str(value).strip().lower() for value in raw.get("aliases", ()))
    kb_documents = tuple(raw.get("kb_documents") or ())
    kb_by_id = {}
    for index, document in enumerate(kb_documents):
        if not isinstance(document, dict) or not document.get("content"):
            raise ValueError(f"kb_documents[{index}] requires non-empty content")
        document_id = str(document.get("id", "")).strip()
        if not document_id:
            raise ValueError(f"kb_documents[{index}] requires a globally unique id")
        if document_id in kb_by_id:
            raise ValueError(f"Duplicate KB document id: {document_id}")
        kb_by_id[document_id] = document
        refers_to = document.get("refers_to", [])
        if not isinstance(refers_to, list) or not all(
            isinstance(value, str) and value.strip() for value in refers_to
        ):
            raise ValueError(f"kb_documents[{index}].refers_to must be a list of IDs")
        if refers_to and not document_id:
            raise ValueError(f"kb_documents[{index}] needs an id when using refers_to")

    for document_id, document in kb_by_id.items():
        for referenced_id in document.get("refers_to", []):
            if referenced_id not in kb_by_id:
                raise ValueError(
                    f"KB document {document_id!r} refers to unknown id {referenced_id!r}"
                )
            if document.get("include_in_cross_dataset", False) and not kb_by_id[referenced_id].get(
                "include_in_cross_dataset", False
            ):
                raise ValueError(
                    f"Cross-dataset KB document {document_id!r} links to "
                    f"non-cross document {referenced_id!r}"
                )

    return DatasetPlugin(
        name=name,
        aliases=aliases,
        table_prefix=table_prefix,
        entity_key=entity_key,
        create_tables_entrypoint=str(loader.get("create_tables", "")),
        load_data_entrypoint=str(loader.get("load_data", "")),
        loader_type=loader_type,
        tables=tables,
        include_in_cross_dataset=bool(raw.get("include_in_cross_dataset", True)),
        schema_metadata=schema_metadata,
        kb_documents=kb_documents,
        harmonized_views=harmonized_views,
        sql_guideline=str(raw.get("sql_guideline", "")).strip(),
        manifest_path=manifest_path,
    )


def load_plugin_manifest(path: Path, resolve_entrypoints: bool = False) -> DatasetPlugin:
    with path.open("r", encoding="utf-8") as handle:
        raw = yaml.safe_load(handle) or {}
    plugin = plugin_from_mapping(raw, str(path))
    if resolve_entrypoints:
        plugin.create_tables_fn
        plugin.load_all_data_fn
    return plugin


def discover_plugin_manifests(directory: Path = REGISTERED_DATASET_DIR) -> List[DatasetPlugin]:
    if not directory.exists():
        return []
    plugins = []
    for path in sorted(directory.glob("*.yaml")):
        plugins.append(load_plugin_manifest(path))
    return plugins


def validate_unique_plugins(plugins: Iterable[DatasetPlugin]) -> None:
    plugins = list(plugins)
    names: Dict[str, str] = {}
    prefixes: Dict[str, str] = {}
    aliases: Dict[str, str] = {}
    for plugin in plugins:
        normalized_name = plugin.name.lower()
        if normalized_name in names:
            raise ValueError(f"Duplicate dataset name: {plugin.name}")
        if plugin.table_prefix in prefixes:
            raise ValueError(f"Duplicate table prefix: {plugin.table_prefix}")
        names[normalized_name] = plugin.name
        prefixes[plugin.table_prefix] = plugin.name
        for alias in set((normalized_name, *plugin.aliases)):
            if alias in aliases and aliases[alias] != plugin.name:
                raise ValueError(
                    f"Dataset alias {alias!r} is used by both "
                    f"{aliases[alias]} and {plugin.name}"
                )
            aliases[alias] = plugin.name
    validate_global_kb_ids(plugins)


def validate_global_kb_ids(plugins: Iterable[DatasetPlugin], builtin_documents=()) -> None:
    """Reject KB IDs that collide across built-ins or installed plugins."""
    owners: Dict[str, str] = {}
    for document in builtin_documents:
        metadata = getattr(document, "metadata", {}) or {}
        document_id = str(metadata.get("id", "")).strip()
        if not document_id:
            continue
        if document_id in owners:
            raise ValueError(f"Duplicate built-in KB id: {document_id}")
        owners[document_id] = str(metadata.get("dataset_scope", "built-in"))

    for plugin in plugins:
        for document in plugin.kb_documents:
            document_id = str(document["id"]).strip()
            if document_id in owners:
                raise ValueError(
                    f"KB id {document_id!r} from {plugin.name} conflicts with "
                    f"{owners[document_id]}"
                )
            owners[document_id] = plugin.name

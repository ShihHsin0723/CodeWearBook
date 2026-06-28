"""Central registry for built-in and manifest-defined datasets."""

from typing import Dict, Iterable, List

from dataset_plugins import DatasetPlugin, discover_plugin_manifests, validate_unique_plugins
from harmonized_views import create_harmonized_views


SCOPE_BUMP = "BUMP"
SCOPE_SINC = "SinC"
SCOPE_CROSS = "Cross-dataset"

# Backward-compatible public name used by existing callers.
DatasetConfig = DatasetPlugin


BUILTIN_DATASETS = (
    DatasetPlugin(
        name=SCOPE_BUMP,
        aliases=("bump",),
        table_prefix="bump_",
        entity_key="record_id",
        create_tables_entrypoint="create_and_load_BUMP_tables.create_tables",
        load_data_entrypoint="create_and_load_BUMP_tables.load_all_data_from_s3",
        sql_guideline=(
            "Join BUMP participant-level tables on record_id unless the schema "
            "explicitly requires another key."
        ),
    ),
    DatasetPlugin(
        name=SCOPE_SINC,
        aliases=("sinc", "sin c"),
        table_prefix="sinc_",
        entity_key="user_id",
        create_tables_entrypoint="create_and_load_SinC_tables.create_tables",
        load_data_entrypoint="create_and_load_SinC_tables.load_all_data_from_s3",
        sql_guideline=(
            "Join SinC participant-level tables on user_id unless the schema "
            "explicitly requires another key."
        ),
    ),
)


def get_registered_datasets() -> Dict[str, DatasetPlugin]:
    plugins = [*BUILTIN_DATASETS, *discover_plugin_manifests()]
    validate_unique_plugins(plugins)
    return {plugin.name: plugin for plugin in plugins}


# A compatibility snapshot. New code should call get_registered_datasets so a
# manifest installed during a long-running process can be discovered.
DATASET_REGISTRY = get_registered_datasets()


def iter_cross_dataset_plugins() -> Iterable[DatasetPlugin]:
    return (
        plugin for plugin in get_registered_datasets().values()
        if plugin.include_in_cross_dataset
    )


def create_cross_dataset_tables(engine) -> None:
    """Create raw tables for every dataset participating in cross mode."""
    for plugin in iter_cross_dataset_plugins():
        plugin.create_tables_fn(engine)


def load_cross_dataset_data(engine) -> None:
    """Load every participating dataset and rebuild harmonized views."""
    plugins = list(iter_cross_dataset_plugins())
    for plugin in plugins:
        plugin.load_all_data_fn(engine)
    create_harmonized_views(engine, plugins)


def normalize_scope_name(scope: str) -> str:
    if not scope:
        raise ValueError("Dataset scope is required.")

    normalized = scope.strip().lower()
    if normalized in {"cross", "cross-dataset", "cross dataset"}:
        return SCOPE_CROSS

    datasets = list(get_registered_datasets().values())
    numeric_choices = {str(index): plugin.name for index, plugin in enumerate(datasets, 1)}
    numeric_choices[str(len(datasets) + 1)] = SCOPE_CROSS
    if normalized in numeric_choices:
        return numeric_choices[normalized]

    for plugin in datasets:
        if normalized in {plugin.name.lower(), *plugin.aliases}:
            return plugin.name
    raise ValueError(f"Unsupported dataset scope: {scope}")


def get_dataset_config(scope: str) -> DatasetPlugin:
    normalized_scope = normalize_scope_name(scope)
    if normalized_scope == SCOPE_CROSS:
        return DatasetPlugin(
            name=SCOPE_CROSS,
            aliases=("cross", "cross-dataset", "cross dataset"),
            table_prefix="wearable_",
            entity_key="participant_key",
            create_tables_entrypoint="dataset_registry.create_cross_dataset_tables",
            load_data_entrypoint="dataset_registry.load_cross_dataset_data",
            include_in_cross_dataset=False,
            sql_guideline=(
                "Use only harmonized wearable_* views. Compare cohorts by dataset "
                "and use participant_key for participant-level joins."
            ),
        )
    return get_registered_datasets()[normalized_scope]


def get_dataset_scopes(include_cross: bool = True) -> List[str]:
    scopes = list(get_registered_datasets())
    if include_cross:
        scopes.append(SCOPE_CROSS)
    return scopes


def get_raw_table_prefixes() -> List[str]:
    return [plugin.table_prefix for plugin in get_registered_datasets().values()]


def get_schema_metadata() -> Dict[str, dict]:
    metadata = {}
    for plugin in get_registered_datasets().values():
        metadata.update(plugin.schema_metadata)
    return metadata


def create_tables_for_scope(engine, scope: str) -> None:
    get_dataset_config(scope).create_tables_fn(engine)


def load_data_for_scope(engine, scope: str) -> None:
    get_dataset_config(scope).load_all_data_fn(engine)

#!/usr/bin/env python3
"""Validate and install a dataset manifest into the project registry."""

import argparse
import shutil
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from dataset_plugins import (  # noqa: E402
    REGISTERED_DATASET_DIR,
    discover_plugin_manifests,
    load_plugin_manifest,
    validate_global_kb_ids,
    validate_unique_plugins,
)
from dataset_registry import BUILTIN_DATASETS  # noqa: E402
from kb_registry import get_builtin_kb_documents_for_validation  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description="Validate and register a dataset manifest.")
    parser.add_argument("manifest", type=Path)
    parser.add_argument("--replace", action="store_true")
    parser.add_argument(
        "--check-only",
        action="store_true",
        help="Validate the manifest and loader entrypoints without installing it.",
    )
    args = parser.parse_args()

    source = args.manifest.resolve()
    if not source.is_file():
        parser.error(f"Manifest does not exist: {source}")

    candidate = load_plugin_manifest(source, resolve_entrypoints=True)
    builtin_kb_documents = get_builtin_kb_documents_for_validation()
    if args.check_only:
        plugins = [
            *BUILTIN_DATASETS,
            *discover_plugin_manifests(),
            candidate,
        ]
        validate_unique_plugins(plugins)
        validate_global_kb_ids(plugins, builtin_kb_documents)
        print(f"Manifest is valid: {candidate.name}")
        return

    REGISTERED_DATASET_DIR.mkdir(parents=True, exist_ok=True)
    destination = REGISTERED_DATASET_DIR / f"{candidate.name.lower().replace(' ', '_')}.yaml"
    if destination.exists() and not args.replace:
        parser.error(f"Already registered: {destination}. Use --replace to update it.")

    existing = [
        plugin for plugin in discover_plugin_manifests()
        if Path(plugin.manifest_path) != destination
    ]
    plugins = [*BUILTIN_DATASETS, *existing, candidate]
    validate_unique_plugins(plugins)
    validate_global_kb_ids(plugins, builtin_kb_documents)
    shutil.copyfile(source, destination)
    print(f"Registered {candidate.name} at {destination}")
    print("Reinitialize the selected database scope to create and load its tables.")


if __name__ == "__main__":
    main()

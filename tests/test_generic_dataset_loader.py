import tempfile
import unittest
import subprocess
import sys
from pathlib import Path

import pandas as pd
import yaml
from sqlalchemy import create_engine, text

from dataset_plugins import plugin_from_mapping
from generic_dataset_loader import create_tables, inspect_table_schema, load_data


class GenericDatasetLoaderTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.csv_path = Path(self.temp_dir.name) / "sleep.csv"
        pd.DataFrame({
            "User ID": ["001", "002"],
            "Summary Date": ["2026-01-01", "2026-01-02"],
            "Sleep Score": [81, 77],
            "Duration Minutes": [420.5, 390.0],
        }).to_csv(self.csv_path, index=False)
        self.raw_manifest = {
            "name": "TestStudy",
            "aliases": ["test-study"],
            "table_prefix": "test_study_",
            "entity_key": "participant_id",
            "loader": {"type": "tabular"},
            "tables": [{
                "name": "test_study_sleep",
                "source": {"uri": str(self.csv_path), "format": "csv"},
                "infer_unlisted_columns": True,
                "date_columns": ["event_date"],
                "columns": {
                    "participant_id": {
                        "source": "User ID", "type": "text", "required": True,
                    },
                    "event_date": {
                        "source": "Summary Date", "type": "date", "required": True,
                    },
                },
            }],
        }

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_inspection_returns_metadata_without_values(self):
        plugin = plugin_from_mapping(self.raw_manifest)
        metadata = inspect_table_schema(plugin.tables[0], plugin.entity_key)
        self.assertEqual(metadata["participant_id"], "text")
        self.assertEqual(metadata["sleep_score"], "integer")
        self.assertNotIn("001", repr(metadata))
        self.assertNotIn("2026-01-01", repr(metadata))

    def test_generic_loader_creates_and_loads_table(self):
        plugin = plugin_from_mapping(self.raw_manifest)
        engine = create_engine("sqlite:///:memory:")
        create_tables(engine, plugin)
        load_data(engine, plugin)
        with engine.connect() as connection:
            rows = connection.execute(text(
                "SELECT participant_id, sleep_score FROM test_study_sleep "
                "ORDER BY participant_id"
            )).fetchall()
        self.assertEqual(rows, [("001", 81), ("002", 77)])

    def test_inspector_manifest_contains_no_source_values(self):
        output = Path(self.temp_dir.name) / "generated.yaml"
        subprocess.run([
            sys.executable,
            "scripts/datasets/inspect_source.py",
            "--dataset", "GeneratedStudy",
            "--entity-key", "participant_id",
            "--entity-source", "User ID",
            "--source", f"sleep={self.csv_path}",
            "--output", str(output),
        ], check=True, capture_output=True, text=True)
        manifest_text = output.read_text(encoding="utf-8")
        manifest = yaml.safe_load(manifest_text)
        self.assertEqual(manifest["loader"]["type"], "tabular")
        self.assertNotIn("001", manifest_text)
        self.assertNotIn("2026-01-01", manifest_text)


if __name__ == "__main__":
    unittest.main()

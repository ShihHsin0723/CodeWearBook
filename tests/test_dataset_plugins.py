import tempfile
import unittest
from pathlib import Path

from dataset_plugins import (
    DatasetPlugin,
    load_plugin_manifest,
    plugin_from_mapping,
    validate_global_kb_ids,
    validate_unique_plugins,
)
from harmonized_views import _extend_view_sql


class DatasetPluginTests(unittest.TestCase):
    def test_example_manifest_is_complete_and_entrypoints_resolve(self):
        plugin = load_plugin_manifest(
            Path("datasets/examples/dataset.example.yaml"), resolve_entrypoints=True
        )
        self.assertEqual(plugin.table_prefix, "example_")
        self.assertIn("wearable_sleep_daily", plugin.harmonized_views)
        self.assertTrue(plugin.kb_documents)
        self.assertIn("example_sleep", plugin.schema_metadata)

    def test_manifest_rejects_unknown_harmonized_view(self):
        raw = {
            "name": "Test",
            "table_prefix": "test_",
            "entity_key": "participant_id",
            "loader": {
                "create_tables": "datasets.example_dataset_adapter.create_tables",
                "load_data": "datasets.example_dataset_adapter.load_data",
            },
            "harmonized_views": {"wearable_unknown": "SELECT * FROM test_rows"},
        }
        with self.assertRaisesRegex(ValueError, "Unsupported harmonized views"):
            plugin_from_mapping(raw)

    def test_manifest_rejects_incomplete_harmonized_mapping(self):
        raw = {
            "name": "Test",
            "table_prefix": "test_",
            "entity_key": "participant_id",
            "loader": {
                "create_tables": "datasets.example_dataset_adapter.create_tables",
                "load_data": "datasets.example_dataset_adapter.load_data",
            },
            "harmonized_views": {
                "wearable_sleep_daily": "SELECT value AS dataset FROM test_sleep"
            },
        }
        with self.assertRaisesRegex(ValueError, "missing canonical aliases"):
            plugin_from_mapping(raw)

    def test_manifest_rejects_broken_kb_link(self):
        raw = {
            "name": "Test",
            "table_prefix": "test_",
            "entity_key": "participant_id",
            "loader": {
                "create_tables": "datasets.example_dataset_adapter.create_tables",
                "load_data": "datasets.example_dataset_adapter.load_data",
            },
            "kb_documents": [{
                "id": "scoring",
                "content": "A scoring rule.",
                "refers_to": ["missing_information"],
            }],
        }
        with self.assertRaisesRegex(ValueError, "refers to unknown id"):
            plugin_from_mapping(raw)

    def test_example_manifest_preserves_kb_links(self):
        plugin = load_plugin_manifest(Path("datasets/examples/dataset.example.yaml"))
        scoring = next(
            item for item in plugin.kb_documents
            if item["id"] == "example_sleep_score_interpretation"
        )
        self.assertEqual(scoring["refers_to"], ["example_sleep_granularity"])

    def test_global_kb_id_collision_is_rejected(self):
        plugin_a = DatasetPlugin(
            name="A", aliases=("a",), table_prefix="a_", entity_key="id",
            create_tables_entrypoint="datasets.example_dataset_adapter.create_tables",
            load_data_entrypoint="datasets.example_dataset_adapter.load_data",
            kb_documents=({"id": "shared_rule", "content": "A rule."},),
        )
        plugin_b = DatasetPlugin(
            name="B", aliases=("b",), table_prefix="b_", entity_key="id",
            create_tables_entrypoint="datasets.example_dataset_adapter.create_tables",
            load_data_entrypoint="datasets.example_dataset_adapter.load_data",
            kb_documents=({"id": "shared_rule", "content": "Another rule."},),
        )
        with self.assertRaisesRegex(ValueError, "conflicts"):
            validate_global_kb_ids([plugin_a, plugin_b])

    def test_plugin_mapping_is_appended_to_harmonized_view(self):
        plugin = load_plugin_manifest(Path("datasets/examples/dataset.example.yaml"))
        sql = _extend_view_sql(
            "CREATE VIEW wearable_sleep_daily AS SELECT 1 AS value;",
            "wearable_sleep_daily",
            [plugin],
        )
        self.assertIn("UNION ALL", sql)
        self.assertIn("FROM example_sleep", sql)
        self.assertTrue(sql.endswith(";"))

    def test_duplicate_prefix_is_rejected(self):
        plugin = load_plugin_manifest(Path("datasets/examples/dataset.example.yaml"))
        with self.assertRaisesRegex(ValueError, "Duplicate dataset name"):
            validate_unique_plugins([plugin, plugin])


if __name__ == "__main__":
    unittest.main()

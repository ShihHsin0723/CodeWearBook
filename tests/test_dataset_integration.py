import unittest
from unittest.mock import patch

from sqlalchemy import create_engine, text

from dataset_plugins import DatasetPlugin
from generate_schema import generate_schema_description
from kb_registry import build_scoped_kb_documents, get_scope_sql_guideline_doc


class DatasetIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.plugin = DatasetPlugin(
            name="ExampleStudy",
            aliases=("example",),
            table_prefix="example_",
            entity_key="participant_id",
            create_tables_entrypoint="datasets.example_dataset_adapter.create_tables",
            load_data_entrypoint="datasets.example_dataset_adapter.load_data",
            schema_metadata={
                "example_sleep": {
                    "description": "Semantic sleep description.",
                    "keywords": ["sleep quality", "rmssd"],
                    "columns": {
                        "rmssd": {
                            "description": "Nightly heart-rate variability.",
                            "keywords": ["recovery"],
                            "aliases": ["HRV"],
                            "unit": "ms",
                            "null_meaning": "No valid nightly HRV measurement.",
                        }
                    },
                }
            },
            kb_documents=(
                {
                    "id": "example_rule",
                    "type": "table_info",
                    "table": "example_sleep",
                    "content": "Use one row per participant and date.",
                },
            ),
            sql_guideline="Join participant tables on participant_id.",
        )

    def test_manifest_schema_metadata_overlays_introspection(self):
        engine = create_engine("sqlite:///:memory:")
        with engine.begin() as connection:
            connection.execute(text(
                "CREATE TABLE example_sleep "
                "(participant_id TEXT, event_date TEXT, rmssd REAL)"
            ))

        with patch(
            "dataset_registry.get_schema_metadata",
            return_value=self.plugin.schema_metadata,
        ):
            docs = generate_schema_description(engine)

        document = next(doc for doc in docs if doc.metadata["table"] == "example_sleep")
        self.assertIn("Semantic sleep description.", document.page_content)
        self.assertIn("Nightly heart-rate variability.", document.page_content)
        self.assertIn("[UNIT: ms]", document.page_content)
        self.assertIn("recovery", document.metadata["keywords"])
        self.assertIn("HRV", document.metadata["keywords"])
        self.assertEqual(
            document.metadata["column_metadata"]["rmssd"]["unit"], "ms"
        )

    def test_manifest_kb_and_sql_guideline_are_scoped(self):
        registry = {self.plugin.name: self.plugin}
        with patch("kb_registry.get_registered_datasets", return_value=registry):
            documents = build_scoped_kb_documents(self.plugin.name)
        rule = next(doc for doc in documents if doc.metadata.get("id") == "example_rule")
        self.assertEqual(rule.metadata["dataset_scope"], self.plugin.name)

        with patch("kb_registry.get_dataset_config", return_value=self.plugin):
            guideline = get_scope_sql_guideline_doc(self.plugin.name)
        self.assertIn("participant_id", guideline.page_content)


if __name__ == "__main__":
    unittest.main()

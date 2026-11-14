"""Execute the actual prompt demonstrations on synthetic, schema-matched data."""

import ast
import json
from pathlib import Path
import re
import sqlite3
import unittest

from sql_scope_validation import validate_sql_for_scope


ROOT = Path(__file__).resolve().parents[1]


def example_blocks():
    # Load only the pure example-builder function; do not initialize models/APIs.
    tree = ast.parse((ROOT / "query_system.py").read_text())
    node = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "_build_scope_sql_examples")
    namespace = {"SCOPE_BUMP": "BUMP", "SCOPE_SINC": "SinC", "SCOPE_CROSS": "Cross-dataset"}
    exec(compile(ast.Module(body=[node], type_ignores=[]), "query_system.py", "exec"), namespace)
    return {scope: re.findall(r"User Query:\s*(.*?)\n\s*SQL:\s*```SQL\s*(.*?)```", namespace[node.name](scope), re.S)
            for scope in ("BUMP", "SinC", "Cross-dataset")}


class FewShotExampleTests(unittest.TestCase):
    def setUp(self):
        self.connection = sqlite3.connect(":memory:")
        self.addCleanup(self.connection.close)
        self.examples = example_blocks()
        for directory in ("bump", "sinc", "cross_dataset"):
            path = ROOT / "prompt_simulation" / directory / "schema_index.jsonl"
            for line in path.read_text().splitlines():
                schema = json.loads(line)
                types = schema.get("column_types", {})
                columns = []
                for name in schema["columns"]:
                    dtype = {"integer": "INTEGER", "float": "REAL", "text": "TEXT", "datetime": "TEXT"}.get(types.get(name), "")
                    columns.append('"' + name.replace('"', '""') + '" ' + dtype)
                self.connection.execute(f'CREATE TABLE "{schema["sql_table"]}" ({", ".join(columns)})')

    def insert(self, table, columns, rows):
        quoted = ", ".join('"' + c + '"' for c in columns)
        placeholders = ", ".join("?" for _ in columns)
        self.connection.executemany(f'INSERT INTO "{table}" ({quoted}) VALUES ({placeholders})', rows)

    def run_example(self, scope, number):
        sql = self.examples[scope][number - 1][1]
        validate_sql_for_scope(sql, scope)
        return self.connection.execute(sql).fetchall()

    def test_all_main_examples_match_schema_and_scope(self):
        self.assertEqual({k: len(v) for k, v in self.examples.items()}, {"BUMP": 4, "SinC": 4, "Cross-dataset": 6})
        for scope, examples in self.examples.items():
            for question, sql in examples:
                with self.subTest(scope=scope, question=question):
                    validate_sql_for_scope(sql, scope)
                    self.connection.execute("EXPLAIN " + sql).fetchall()

    def test_bump_duplicate_calendar_dates(self):
        self.insert("bump_oura_sleep", ["record_id", "event_date"], [
            ("A", "2024-01-01 01:00:00"), ("A", "2024-01-01 22:00:00"),
            ("A", "2024-01-02"), ("B", "2024-01-01"),
            (None, "2024-01-01"), ("A", "invalid"),
        ])
        self.assertEqual(self.run_example("BUMP", 3), [("A", "2024-01-01", 2)])

    def test_bump_missing_same_day_sleep_deduplicates_activity(self):
        self.insert("bump_oura_activity", ["record_id", "event_date"], [
            ("A", "2024-01-01 12:00:00"), ("A", "2024-01-02 01:00:00"),
            ("A", "2024-01-02 20:00:00"), ("B", "2024-01-01"), (None, "2024-01-01"),
        ])
        self.insert("bump_oura_sleep", ["record_id", "event_date"], [("A", "2024-01-01 22:00:00"), ("B", "2024-01-02")])
        self.assertEqual(self.run_example("BUMP", 4), [("A", "2024-01-02"), ("B", "2024-01-01")])

    def test_sinc_monthly_weight_range(self):
        self.insert("sinc_bodyport", ["user_id", "event_date", "weight_kg"], [
            ("A", "2024-01-01", 80.0), ("A", "2024-01-20", 82.0),
            ("A", "2024-02-01", 79.0), ("A", "2024-01-21", None),
            ("B", "2024-01-02", 100.0), ("B", "invalid", 90.0),
        ])
        self.assertEqual(self.run_example("SinC", 1), [
            ("A", "2024-01", 80.0, 82.0, 2.0), ("A", "2024-02", 79.0, 79.0, 0.0),
            ("B", "2024-01", 100.0, 100.0, 0.0),
        ])

    def test_sinc_answer_levels_exclude_invalid_and_difficulty_answers(self):
        self.insert("sinc_phq_8", ["question", "answer"], [
            ("Interest", "Several days (1)"), ("Mood", "Not at all (0)"),
            ("Mood", "Not at all (0)"), ("Mood", "Nearly every day (3)"),
            ("Mood", "Unknown"), ("Mood", None), ("How difficult was this?", "Not at all (0)"),
        ])
        self.assertEqual(self.run_example("SinC", 2), [("Interest", 1, 1), ("Mood", 0, 2), ("Mood", 3, 1)])

    def test_sinc_monthly_eim_response_coverage(self):
        self.insert("sinc_extraintestinal_manifestations", ["user_id", "date", "answer"], [
            ("A", "2024-01-01", " Arthritis "), ("A", "2024-01-02", "Arthritis"),
            ("A", "2024-02-01", "Ulcers"), ("C", "2024-01-01", " "),
            ("C", "2024-01-01", None), ("B", "2024-01-10", "Ulcers"),
            (None, "2024-01-01", "Arthritis"), ("D", "invalid", "Ulcers"),
        ])
        self.assertEqual(self.run_example("SinC", 4), [("2024-01", 2), ("2024-02", 1)])

    def test_retained_phq_classification_uses_actual_assessment_date_column(self):
        rows = []
        for day, levels in (("2024-01-01", [0] * 8), ("2024-01-02", [2] * 5 + [0] * 3), ("2024-01-03", [1] * 7)):
            rows.extend(("X", day, f"Item {index}", f"Response ({level})") for index, level in enumerate(levels))
        self.insert("sinc_phq_8", ["user_id", "date", "question", "answer"], rows)
        self.assertEqual(self.run_example("SinC", 3), [
            ("X", "2024-01-01", 0, "Minimal depression"),
            ("X", "2024-01-02", 10, "Moderate depression"),
        ])

    def test_cross_scored_sleep_date_bounds(self):
        self.insert("wearable_sleep_daily", ["dataset", "event_date", "sleep_score"], [
            ("BUMP", "2024-01-01", None), ("BUMP", "2024-01-02 22:00:00", 75),
            ("BUMP", "2024-01-04", 80), ("BUMP", "invalid", 90), ("SinC", "2024-02-01", 70),
        ])
        self.assertEqual(self.run_example("Cross-dataset", 1), [("BUMP", "2024-01-02", "2024-01-04"), ("SinC", "2024-02-01", "2024-02-01")])

    def test_cross_readiness_missing_hrv_balance(self):
        self.insert("wearable_readiness_daily", ["dataset", "readiness_score", "score_hrv_balance"], [
            ("BUMP", 70, None), ("BUMP", 80, None), ("BUMP", None, None),
            ("BUMP", 80, 60), ("SinC", 65, None), ("SinC", 70, 0),
        ])
        self.assertEqual(self.run_example("Cross-dataset", 3), [("BUMP", 2), ("SinC", 1)])

    def test_no_identical_questions_or_sql_in_versioned_benchmarks(self):
        def normalize_question(value):
            return re.sub(r"\W+", " ", value.lower()).strip()

        def normalize_sql(value):
            # Preserve operators and literal values; remove only formatting.
            return re.sub(r"\s+", "", value).rstrip(";").casefold()

        questions, statements = set(), set()
        for path in (ROOT / "prompts").rglob("*.jsonl"):
            for line in path.read_text().splitlines():
                row = json.loads(line)
                questions.add(normalize_question(row.get("question", "")))
                statements.add(normalize_sql(row.get("sql", "")))
        for scope, examples in self.examples.items():
            for question, sql in examples:
                with self.subTest(scope=scope, question=question):
                    self.assertNotIn(normalize_question(question), questions)
                    self.assertNotIn(normalize_sql(sql), statements)


if __name__ == "__main__":
    unittest.main()

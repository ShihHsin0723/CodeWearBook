import unittest

from sql_scope_validation import validate_sql_for_scope


class SqlScopeValidationTests(unittest.TestCase):
    def test_standalone_scope_allows_its_own_tables(self):
        validate_sql_for_scope(
            "SELECT record_id FROM bump_oura_sleep", "BUMP"
        )

    def test_standalone_scope_rejects_another_dataset(self):
        with self.assertRaisesRegex(ValueError, "Disallowed tables: sinc_oura_sleep"):
            validate_sql_for_scope(
                "SELECT user_id FROM sinc_oura_sleep", "BUMP"
            )

    def test_cte_is_allowed_but_its_physical_table_is_checked(self):
        validate_sql_for_scope(
            "WITH daily AS (SELECT * FROM bump_oura_sleep) SELECT * FROM daily",
            "BUMP",
        )
        with self.assertRaisesRegex(ValueError, "sinc_oura_sleep"):
            validate_sql_for_scope(
                "WITH daily AS (SELECT * FROM sinc_oura_sleep) SELECT * FROM daily",
                "BUMP",
            )

    def test_cross_scope_rejects_raw_and_unrelated_tables(self):
        with self.assertRaisesRegex(ValueError, "bump_oura_sleep"):
            validate_sql_for_scope("SELECT * FROM bump_oura_sleep", "Cross-dataset")
        with self.assertRaisesRegex(ValueError, "sqlite_master"):
            validate_sql_for_scope(
                "SELECT * FROM wearable_sleep_daily JOIN sqlite_master ON 1=1",
                "Cross-dataset",
            )

    def test_cross_scope_allows_harmonized_views(self):
        validate_sql_for_scope(
            "SELECT dataset, AVG(sleep_score) FROM wearable_sleep_daily GROUP BY dataset",
            "Cross-dataset",
        )

    def test_mutating_sql_is_rejected(self):
        for sql in (
            "DELETE FROM bump_oura_sleep",
            "DROP TABLE bump_oura_sleep",
            "WITH rows AS (SELECT * FROM bump_oura_sleep) DELETE FROM bump_oura_sleep",
        ):
            with self.subTest(sql=sql), self.assertRaisesRegex(ValueError, "read-only|Read-only"):
                validate_sql_for_scope(sql, "BUMP")

    def test_implicit_comma_join_cannot_bypass_scope_check(self):
        with self.assertRaisesRegex(ValueError, "explicit JOIN"):
            validate_sql_for_scope(
                "SELECT * FROM bump_oura_sleep AS b, sinc_oura_sleep AS s",
                "BUMP",
            )

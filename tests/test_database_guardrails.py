from pathlib import Path
import sqlite3
import tempfile
import unittest

from sqlalchemy import text
from sqlalchemy.exc import OperationalError

from database_guardrails import create_read_only_sqlite_engine


class ReadOnlyDatabaseTests(unittest.TestCase):
    def test_existing_file_allows_reads_and_blocks_writes(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "synthetic # database.sqlite"
            with sqlite3.connect(path) as connection:
                connection.execute("CREATE TABLE synthetic (value INTEGER)")
                connection.execute("INSERT INTO synthetic VALUES (7)")
            engine = create_read_only_sqlite_engine(f"sqlite:///{path}")
            try:
                with engine.connect() as connection:
                    self.assertEqual(connection.execute(text("SELECT value FROM synthetic")).scalar(), 7)
                    for sql in ("INSERT INTO synthetic VALUES (8)", "DROP TABLE synthetic", "CREATE TEMP TABLE unsafe (x)"):
                        with self.subTest(sql=sql), self.assertRaises(OperationalError):
                            connection.execute(text(sql))
                # File mode remains read-only even if query_only is disabled.
                with engine.connect() as connection:
                    connection.execute(text("PRAGMA query_only = OFF"))
                    with self.assertRaises(OperationalError):
                        connection.execute(text("INSERT INTO synthetic VALUES (8)"))
            finally:
                engine.dispose()

    def test_missing_file_is_not_created(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "absent.sqlite"
            engine = create_read_only_sqlite_engine(f"sqlite:///{path}")
            try:
                with self.assertRaises(OperationalError):
                    with engine.connect():
                        pass
                self.assertFalse(path.exists())
            finally:
                engine.dispose()


if __name__ == "__main__":
    unittest.main()

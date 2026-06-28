"""Example loader adapter. Replace this module with real ingestion logic."""

from sqlalchemy import text


def create_tables(engine):
    with engine.begin() as connection:
        connection.execute(text("""
            CREATE TABLE IF NOT EXISTS example_sleep (
                participant_id TEXT NOT NULL,
                event_date TEXT NOT NULL,
                sleep_score REAL,
                duration_minutes REAL,
                hr_average REAL,
                hr_lowest REAL,
                rmssd REAL,
                efficiency REAL
            )
        """))


def load_data(engine):
    """Load authorized source data here; the example intentionally loads none."""
    return None

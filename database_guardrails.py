"""Read-only connections for existing shared SQLite databases."""

from pathlib import Path
import sqlite3

from sqlalchemy import create_engine
from sqlalchemy.engine import make_url


def create_read_only_sqlite_engine(connection_string):
    url = make_url(connection_string)
    if url.get_backend_name() != "sqlite" or not url.database or url.database == ":memory:":
        raise ValueError("The shared database must be an existing file-backed SQLite database.")
    uri = Path(url.database).resolve().as_uri() + "?mode=ro"

    def connect():
        connection = sqlite3.connect(uri, uri=True, check_same_thread=False)
        connection.execute("PRAGMA query_only = ON")
        return connection

    return create_engine(url, creator=connect)

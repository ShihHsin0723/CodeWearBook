import argparse
import sys
from pathlib import Path

from sqlalchemy import create_engine

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from config import DB_CONNECTION_STRING, SHARED_SQLITE_PATH
from dataset_registry import create_tables_for_scope, load_data_for_scope, normalize_scope_name


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Initialize and load the shared SQLite database for a selected dataset."
    )
    parser.add_argument(
        "--dataset-scope",
        default="bump",
        help="Registered dataset name/alias or cross-dataset.",
    )
    parser.add_argument(
        "--skip-load",
        action="store_true",
        help="Create the SQLite file path if needed, but do not create tables or load data.",
    )
    args = parser.parse_args()

    SHARED_SQLITE_PATH.parent.mkdir(parents=True, exist_ok=True)
    if SHARED_SQLITE_PATH.exists():
        print(f"Shared SQLite database already exists at {SHARED_SQLITE_PATH}")
    else:
        print(f"Shared SQLite database will be created at {SHARED_SQLITE_PATH}")

    if args.skip_load:
        print("Skipped table creation and data loading.")
        return

    engine = create_engine(DB_CONNECTION_STRING)
    session_scope = normalize_scope_name(args.dataset_scope)
    create_tables_for_scope(engine, session_scope)
    load_data_for_scope(engine, session_scope)
    print("\nShared database is ready.")


if __name__ == "__main__":
    main()

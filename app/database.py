"""Database access helpers for CloudVault.

This module owns connection creation, schema initialisation and schema
verification. It is deliberately independent of authentication and file
management logic so later stages can build on a stable foundation.
"""

import sqlite3
from pathlib import Path

from flask import current_app, g

# The 11 approved application tables. SQLite's internal tables (for example
# ``sqlite_sequence``) are intentionally excluded.
APPLICATION_TABLES = frozenset(
    {
        "users",
        "folders",
        "files",
        "file_versions",
        "tags",
        "file_tags",
        "shares",
        "upload_sessions",
        "backup_checks",
        "activity_logs",
        "login_attempts",
    }
)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_SCHEMA_PATH = PROJECT_ROOT / "schema.sql"
DEFAULT_DB_PATH = PROJECT_ROOT / "instance" / "cloudvault.db"

STORAGE_DIRECTORIES = ("storage", "backup", "temporary_uploads")


def _configure_connection(connection):
    """Apply settings that must hold for every SQLite connection."""
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")


def connect(db_path):
    """Open a connection with foreign-key enforcement enabled.

    The parent directory is created if it does not exist so that callers never
    depend on a manually created database file. The caller owns the returned
    connection and is responsible for closing it.
    """
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(str(path))
    _configure_connection(connection)
    return connection


def get_db():
    """Return the request-scoped database connection (Flask ``g``)."""
    if "db" not in g:
        g.db = connect(current_app.config["DATABASE"])
    return g.db


def close_db(exception=None):
    """Close the request-scoped connection if one was opened."""
    connection = g.pop("db", None)
    if connection is not None:
        connection.close()


def execute_script_atomically(connection, script):
    """Execute a multi-statement SQL script inside one explicit transaction.

    ``sqlite3.Connection.executescript()`` implicitly commits any pending
    transaction before it runs, so a plain ``BEGIN`` issued beforehand would be
    lost. To keep the script atomic, the ``BEGIN IMMEDIATE`` and ``COMMIT`` are
    embedded in the script text itself. If any statement fails, the still-open
    transaction is rolled back, leaving the database unchanged.

    Raises the original ``sqlite3.Error`` after rolling back.
    """
    wrapped = "BEGIN IMMEDIATE;\n" + script + "\nCOMMIT;"
    try:
        connection.executescript(wrapped)
    except sqlite3.Error:
        connection.rollback()
        raise


def init_db(db_path=DEFAULT_DB_PATH, schema_path=DEFAULT_SCHEMA_PATH):
    """Create the database (if needed) and apply the approved schema atomically.

    The operation is idempotent: all schema statements use ``IF NOT EXISTS``,
    so repeated calls neither duplicate tables nor remove existing data.
    Raises ``RuntimeError`` with a clear message if the schema fails to apply.
    A failure leaves the database unchanged (no partially applied schema).
    """
    schema_file = Path(schema_path)
    if not schema_file.is_file():
        raise FileNotFoundError(f"Schema file not found: {schema_file}")

    schema_sql = schema_file.read_text(encoding="utf-8")
    connection = connect(db_path)
    try:
        execute_script_atomically(connection, schema_sql)
    except sqlite3.Error as exc:
        raise RuntimeError(f"Database schema initialisation failed: {exc}") from exc
    finally:
        connection.close()


def list_application_tables(connection):
    """Return the set of non-internal application table names."""
    rows = connection.execute(
        "SELECT name FROM sqlite_master "
        "WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
    ).fetchall()
    return {row[0] for row in rows}


def verify_schema(connection):
    """Compare the live schema against the 11 approved application tables.

    Returns a dictionary describing found, missing and unexpected tables plus
    an overall ``ok`` flag. This makes it easy to surface a clear error when
    the schema is incomplete or contains stray application tables.
    """
    found = list_application_tables(connection)
    missing = APPLICATION_TABLES - found
    unexpected = found - APPLICATION_TABLES
    return {
        "found": sorted(found),
        "missing": sorted(missing),
        "unexpected": sorted(unexpected),
        "ok": not missing and not unexpected,
    }


def foreign_keys_enabled(connection):
    """Return True when foreign-key enforcement is active on ``connection``."""
    return connection.execute("PRAGMA foreign_keys").fetchone()[0] == 1


def ensure_storage_dirs(base_dir=PROJECT_ROOT):
    """Create the approved storage directories and return their paths."""
    base = Path(base_dir)
    created = {}
    for name in STORAGE_DIRECTORIES:
        directory = base / name
        directory.mkdir(parents=True, exist_ok=True)
        created[name] = directory
    return created

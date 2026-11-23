"""Shared pytest fixtures for CloudVault Stage 1 tests."""

import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app import create_app, database  # noqa: E402


@pytest.fixture()
def app(tmp_path):
    """A Flask app backed by an isolated, initialised temporary database."""
    db_path = tmp_path / "instance" / "test_auth.db"
    database.init_db(db_path)
    application = create_app(
        {
            "TESTING": True,
            "SECRET_KEY": "test-secret-key",
            "DATABASE": str(db_path),
            "SCHEMA_PATH": str(database.DEFAULT_SCHEMA_PATH),
            "BASE_DIR": tmp_path,
            "INSTANCE_DIR": tmp_path / "instance",
            "SESSION_COOKIE_SECURE": False,
        }
    )
    yield application


@pytest.fixture()
def client(app):
    return app.test_client()


@pytest.fixture()
def tmp_db_path(tmp_path):
    """A database path inside pytest's temporary directory."""
    return tmp_path / "instance" / "test_cloudvault.db"


@pytest.fixture()
def initialised_db(tmp_db_path):
    """Initialise a temporary database and return its path."""
    database.init_db(tmp_db_path)
    return tmp_db_path


@pytest.fixture()
def connection(initialised_db):
    """An open connection to an initialised temporary database."""
    conn = database.connect(initialised_db)
    try:
        yield conn
    finally:
        conn.close()


def _insert_user(conn, username="alice", email="alice@example.com"):
    cur = conn.execute(
        "INSERT INTO users (username, email, password_hash) VALUES (?, ?, ?)",
        (username, email, "hashed-password-value"),
    )
    conn.commit()
    return cur.lastrowid


def _insert_file(conn, user_id, name="notes.txt"):
    cur = conn.execute(
        "INSERT INTO files (user_id, original_filename, stored_filename) "
        "VALUES (?, ?, ?)",
        (user_id, name, f"stored-{name}"),
    )
    conn.commit()
    return cur.lastrowid

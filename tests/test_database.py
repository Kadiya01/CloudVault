"""Stage 1 tests: database schema, initialisation, constraints, storage dirs."""

import re
import sqlite3

import pytest

from app import database

APPROVED_TABLES = {
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


def insert_user(conn, username="alice", email="alice@example.com", **overrides):
    values = {
        "username": username,
        "email": email,
        "password_hash": "hashed-password-value",
    }
    values.update(overrides)
    cur = conn.execute(
        "INSERT INTO users (username, email, password_hash, role, quota_bytes, is_active) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (
            values["username"],
            values["email"],
            values["password_hash"],
            values.get("role", "user"),
            values.get("quota_bytes", 0),
            values.get("is_active", 1),
        ),
    )
    conn.commit()
    return cur.lastrowid


def insert_file(conn, user_id, folder_id=None, name="notes.txt", status="active",
                deleted_at=None):
    cur = conn.execute(
        "INSERT INTO files "
        "(user_id, folder_id, original_filename, stored_filename, status, deleted_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (user_id, folder_id, name, f"stored-{name}", status, deleted_at),
    )
    conn.commit()
    return cur.lastrowid


def insert_folder(conn, user_id, name="docs", parent_id=None):
    cur = conn.execute(
        "INSERT INTO folders (user_id, parent_id, name) VALUES (?, ?, ?)",
        (user_id, parent_id, name),
    )
    conn.commit()
    return cur.lastrowid


def insert_tag(conn, user_id, name="work"):
    cur = conn.execute(
        "INSERT INTO tags (user_id, name) VALUES (?, ?)", (user_id, name)
    )
    conn.commit()
    return cur.lastrowid


def insert_share(conn, file_id, user_id, token="token-" + "x" * 26,
                 expires_at="2099-01-01T00:00:00.000Z"):
    cur = conn.execute(
        "INSERT INTO shares (file_id, user_id, token, expires_at) VALUES (?, ?, ?, ?)",
        (file_id, user_id, token, expires_at),
    )
    conn.commit()
    return cur.lastrowid


def insert_upload_session(conn, user_id, upload_id, file_id=None, folder_id=None):
    cur = conn.execute(
        "INSERT INTO upload_sessions "
        "(user_id, file_id, folder_id, upload_id, original_filename) "
        "VALUES (?, ?, ?, ?, ?)",
        (user_id, file_id, folder_id, upload_id, "upload.bin"),
    )
    conn.commit()
    return cur.lastrowid


# --------------------------------------------------------------------------
# Initialisation and location
# --------------------------------------------------------------------------

def test_default_database_path_is_instance_cloudvault_db():
    assert database.DEFAULT_DB_PATH == database.PROJECT_ROOT / "instance" / "cloudvault.db"
    assert database.DEFAULT_DB_PATH.name == "cloudvault.db"
    assert database.DEFAULT_DB_PATH.parent.name == "instance"


def test_initialisation_creates_database_file(initialised_db):
    assert initialised_db.is_file()
    assert initialised_db.stat().st_size > 0


def test_initialisation_creates_missing_directories(tmp_path):
    nested = tmp_path / "fresh" / "instance" / "cloudvault.db"
    assert not nested.parent.exists()
    database.init_db(nested)
    assert nested.is_file()


def test_initialisation_is_idempotent_and_preserves_data(initialised_db):
    conn = database.connect(initialised_db)
    try:
        insert_user(conn, username="keeper", email="keeper@example.com")
    finally:
        conn.close()

    database.init_db(initialised_db)

    conn = database.connect(initialised_db)
    try:
        assert len(database.list_application_tables(conn)) == 11
        count = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
        assert count == 1
    finally:
        conn.close()


# --------------------------------------------------------------------------
# Table inventory
# --------------------------------------------------------------------------

def test_exactly_eleven_application_tables(connection):
    found = database.list_application_tables(connection)
    assert len(found) == 11
    assert found == APPROVED_TABLES


def test_all_approved_table_names_exist(connection):
    found = database.list_application_tables(connection)
    for name in APPROVED_TABLES:
        assert name in found


def test_legacy_tables_are_absent(connection):
    found = database.list_application_tables(connection)
    assert "versions" not in found
    assert "activity_log" not in found


def test_sqlite_internal_tables_excluded(connection):
    found = database.list_application_tables(connection)
    assert all(not name.startswith("sqlite_") for name in found)


def test_verify_schema_reports_ok(connection):
    result = database.verify_schema(connection)
    assert result["ok"] is True
    assert result["missing"] == []
    assert result["unexpected"] == []


def test_verify_schema_reports_missing_tables(tmp_path):
    empty_db = tmp_path / "empty.db"
    conn = database.connect(empty_db)
    try:
        result = database.verify_schema(conn)
    finally:
        conn.close()
    assert result["ok"] is False
    assert set(result["missing"]) == APPROVED_TABLES


# --------------------------------------------------------------------------
# Foreign-key enforcement and relationships
# --------------------------------------------------------------------------

def test_foreign_keys_enabled_on_connection(connection):
    assert database.foreign_keys_enabled(connection) is True


def test_foreign_key_rejects_unknown_user(connection):
    with pytest.raises(sqlite3.IntegrityError):
        connection.execute(
            "INSERT INTO folders (user_id, name) VALUES (?, ?)", (9999, "orphan")
        )


def test_foreign_key_rejects_unknown_folder(connection):
    user_id = insert_user(connection)
    with pytest.raises(sqlite3.IntegrityError):
        connection.execute(
            "INSERT INTO files (user_id, folder_id, original_filename, stored_filename) "
            "VALUES (?, ?, ?, ?)",
            (user_id, 9999, "a.txt", "stored-a.txt"),
        )


def test_foreign_key_rejects_unknown_tag_reference(connection):
    user_id = insert_user(connection)
    file_id = insert_file(connection, user_id)
    with pytest.raises(sqlite3.IntegrityError):
        connection.execute(
            "INSERT INTO file_tags (file_id, tag_id, user_id) VALUES (?, ?, ?)",
            (file_id, 9999, user_id),
        )


def test_cascade_delete_removes_owned_records(connection):
    user_id = insert_user(connection)
    folder = connection.execute(
        "INSERT INTO folders (user_id, name) VALUES (?, ?)", (user_id, "docs")
    )
    folder_id = folder.lastrowid
    insert_file(connection, user_id, folder_id=folder_id)

    connection.execute("DELETE FROM users WHERE id = ?", (user_id,))
    connection.commit()

    assert connection.execute("SELECT COUNT(*) FROM folders").fetchone()[0] == 0
    assert connection.execute("SELECT COUNT(*) FROM files").fetchone()[0] == 0


# --------------------------------------------------------------------------
# Cross-user ownership integrity
# --------------------------------------------------------------------------

def _two_users(connection):
    alice = insert_user(connection, username="alice", email="alice@example.com")
    bob = insert_user(connection, username="bob", email="bob@example.com")
    return alice, bob


def test_folder_parent_must_belong_to_same_user(connection):
    alice, bob = _two_users(connection)
    bob_folder = insert_folder(connection, bob, name="bob-docs")
    with pytest.raises(sqlite3.IntegrityError):
        insert_folder(connection, alice, name="alice-child", parent_id=bob_folder)


def test_valid_folder_parent_same_user(connection):
    alice, _ = _two_users(connection)
    parent = insert_folder(connection, alice, name="parent")
    child = insert_folder(connection, alice, name="child", parent_id=parent)
    assert child is not None


def test_file_folder_must_belong_to_same_user(connection):
    alice, bob = _two_users(connection)
    bob_folder = insert_folder(connection, bob, name="bob-docs")
    with pytest.raises(sqlite3.IntegrityError):
        insert_file(connection, alice, folder_id=bob_folder, name="alice-in-bob.txt")


def test_valid_file_folder_same_user(connection):
    alice, _ = _two_users(connection)
    folder = insert_folder(connection, alice, name="alice-docs")
    file_id = insert_file(connection, alice, folder_id=folder, name="ok.txt")
    assert file_id is not None


def test_file_tag_must_belong_to_same_user(connection):
    alice, bob = _two_users(connection)
    alice_file = insert_file(connection, alice, name="alice.txt")
    bob_tag = insert_tag(connection, bob, name="bob-tag")
    with pytest.raises(sqlite3.IntegrityError):
        connection.execute(
            "INSERT INTO file_tags (file_id, tag_id, user_id) VALUES (?, ?, ?)",
            (alice_file, bob_tag, alice),
        )


def test_valid_file_tag_same_user(connection):
    alice, _ = _two_users(connection)
    file_id = insert_file(connection, alice, name="alice.txt")
    tag_id = insert_tag(connection, alice, name="alice-tag")
    connection.execute(
        "INSERT INTO file_tags (file_id, tag_id, user_id) VALUES (?, ?, ?)",
        (file_id, tag_id, alice),
    )
    connection.commit()
    assert connection.execute("SELECT COUNT(*) FROM file_tags").fetchone()[0] == 1


def test_share_user_must_own_file(connection):
    alice, bob = _two_users(connection)
    bob_file = insert_file(connection, bob, name="bob.txt")
    with pytest.raises(sqlite3.IntegrityError):
        insert_share(connection, bob_file, alice, token="a" * 32)


def test_valid_share_same_user(connection):
    alice, _ = _two_users(connection)
    file_id = insert_file(connection, alice, name="alice.txt")
    share_id = insert_share(connection, file_id, alice, token="b" * 32)
    assert share_id is not None


def test_upload_session_rejects_other_users_file(connection):
    alice, bob = _two_users(connection)
    bob_file = insert_file(connection, bob, name="bob.txt")
    with pytest.raises(sqlite3.IntegrityError):
        insert_upload_session(connection, alice, "upload-file", file_id=bob_file)


def test_upload_session_rejects_other_users_folder(connection):
    alice, bob = _two_users(connection)
    bob_folder = insert_folder(connection, bob, name="bob-docs")
    with pytest.raises(sqlite3.IntegrityError):
        insert_upload_session(connection, alice, "upload-folder", folder_id=bob_folder)


def test_valid_upload_session_same_user(connection):
    alice, _ = _two_users(connection)
    folder = insert_folder(connection, alice, name="alice-docs")
    file_id = insert_file(connection, alice, folder_id=folder, name="alice.txt")
    session_id = insert_upload_session(
        connection, alice, "upload-ok", file_id=file_id, folder_id=folder
    )
    assert session_id is not None


# --------------------------------------------------------------------------
# Transaction safety of schema initialisation
# --------------------------------------------------------------------------

def test_init_db_rolls_back_on_schema_error(tmp_path):
    schema = tmp_path / "broken_schema.sql"
    schema.write_text(
        "CREATE TABLE partial_table (id INTEGER PRIMARY KEY);\n"
        "CREATE TABLE broken_table (id INTEGER, id INTEGER);\n",
        encoding="utf-8",
    )
    db_path = tmp_path / "broken.db"

    with pytest.raises(RuntimeError):
        database.init_db(db_path, schema)

    conn = database.connect(db_path)
    try:
        tables = database.list_application_tables(conn)
    finally:
        conn.close()
    assert "partial_table" not in tables
    assert "broken_table" not in tables


def test_failed_init_preserves_existing_data(tmp_path):
    db_path = tmp_path / "existing.db"
    database.init_db(db_path)

    conn = database.connect(db_path)
    try:
        insert_user(conn, username="keeper", email="keeper@example.com")
    finally:
        conn.close()

    schema = tmp_path / "broken_schema.sql"
    schema.write_text("CREATE TABLE broken_table (id INTEGER, id INTEGER);\n",
                      encoding="utf-8")
    with pytest.raises(RuntimeError):
        database.init_db(db_path, schema)

    conn = database.connect(db_path)
    try:
        assert len(database.list_application_tables(conn)) == 11
        assert conn.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 1
    finally:
        conn.close()


# --------------------------------------------------------------------------
# Uniqueness
# --------------------------------------------------------------------------

def test_username_must_be_unique(connection):
    insert_user(connection)
    with pytest.raises(sqlite3.IntegrityError):
        insert_user(connection, email="other@example.com")


def test_email_must_be_unique(connection):
    insert_user(connection)
    with pytest.raises(sqlite3.IntegrityError):
        insert_user(connection, username="bob")


def test_tag_name_unique_per_user(connection):
    user_id = insert_user(connection)
    connection.execute(
        "INSERT INTO tags (user_id, name) VALUES (?, ?)", (user_id, "work")
    )
    connection.commit()
    with pytest.raises(sqlite3.IntegrityError):
        connection.execute(
            "INSERT INTO tags (user_id, name) VALUES (?, ?)", (user_id, "work")
        )


def test_version_number_unique_per_file(connection):
    user_id = insert_user(connection)
    file_id = insert_file(connection, user_id)
    checksum = "a" * 64
    connection.execute(
        "INSERT INTO file_versions "
        "(file_id, version_number, size_bytes, checksum_sha256, backup_path) "
        "VALUES (?, ?, ?, ?, ?)",
        (file_id, 1, 10, checksum, "backup/v1"),
    )
    connection.commit()
    with pytest.raises(sqlite3.IntegrityError):
        connection.execute(
            "INSERT INTO file_versions "
            "(file_id, version_number, size_bytes, checksum_sha256, backup_path) "
            "VALUES (?, ?, ?, ?, ?)",
            (file_id, 1, 20, checksum, "backup/v1b"),
        )


def test_stored_filename_must_be_unique(connection):
    user_id = insert_user(connection)
    connection.execute(
        "INSERT INTO files (user_id, original_filename, stored_filename) "
        "VALUES (?, ?, ?)",
        (user_id, "one.txt", "same-stored-name"),
    )
    connection.commit()
    with pytest.raises(sqlite3.IntegrityError):
        connection.execute(
            "INSERT INTO files (user_id, original_filename, stored_filename) "
            "VALUES (?, ?, ?)",
            (user_id, "two.txt", "same-stored-name"),
        )


# --------------------------------------------------------------------------
# Check constraints
# --------------------------------------------------------------------------

def test_invalid_role_rejected(connection):
    with pytest.raises(sqlite3.IntegrityError):
        insert_user(connection, role="superuser")


def test_negative_quota_rejected(connection):
    with pytest.raises(sqlite3.IntegrityError):
        insert_user(connection, quota_bytes=-1)


def test_invalid_email_rejected(connection):
    with pytest.raises(sqlite3.IntegrityError):
        insert_user(connection, email="not-an-email")


def test_deleted_file_requires_deleted_at(connection):
    user_id = insert_user(connection)
    with pytest.raises(sqlite3.IntegrityError):
        insert_file(connection, user_id, status="deleted", deleted_at=None)


def test_active_file_cannot_have_deleted_at(connection):
    user_id = insert_user(connection)
    with pytest.raises(sqlite3.IntegrityError):
        insert_file(connection, user_id, status="active", deleted_at="2026-01-01T00:00:00.000Z")


def test_upload_progress_cannot_exceed_total(connection):
    user_id = insert_user(connection)
    with pytest.raises(sqlite3.IntegrityError):
        connection.execute(
            "INSERT INTO upload_sessions "
            "(user_id, upload_id, original_filename, total_size, received_bytes, "
            " total_chunks, received_chunks) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (user_id, "upload-1", "big.bin", 100, 150, 10, 5),
        )


def test_share_expiry_must_be_after_creation(connection):
    user_id = insert_user(connection)
    file_id = insert_file(connection, user_id)
    with pytest.raises(sqlite3.IntegrityError):
        connection.execute(
            "INSERT INTO shares (file_id, user_id, token, expires_at, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (file_id, user_id, "x" * 32, "2026-01-01T00:00:00.000Z",
             "2026-02-01T00:00:00.000Z"),
        )


# --------------------------------------------------------------------------
# Timestamps and storage directories
# --------------------------------------------------------------------------

def test_timestamps_are_iso8601_utc(connection):
    user_id = insert_user(connection)
    created_at = connection.execute(
        "SELECT created_at FROM users WHERE id = ?", (user_id,)
    ).fetchone()[0]
    assert re.fullmatch(
        r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z", created_at
    )


def test_required_storage_directories_created(tmp_path):
    created = database.ensure_storage_dirs(tmp_path)
    for name in ("storage", "backup", "temporary_uploads"):
        assert created[name].is_dir()
        assert created[name] == tmp_path / name


def test_ensure_storage_dirs_is_idempotent(tmp_path):
    database.ensure_storage_dirs(tmp_path)
    database.ensure_storage_dirs(tmp_path)
    assert (tmp_path / "storage").is_dir()
    assert (tmp_path / "backup").is_dir()
    assert (tmp_path / "temporary_uploads").is_dir()

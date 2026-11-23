-- CloudVault database schema (Stage 1.1)
--
-- Exactly 11 application tables are defined here:
--   users, folders, files, file_versions, tags, file_tags, shares,
--   upload_sessions, backup_checks, activity_logs, login_attempts
--
-- Conventions:
--   * Timestamps are stored as ISO-8601 UTC text (e.g. 2026-01-31T12:00:00.000Z)
--     produced by SQLite's strftime with the 'Z' suffix.
--   * Foreign-key enforcement is enabled per connection in app/database.py.
--   * Every statement is idempotent (IF NOT EXISTS) so initialisation can be
--     re-run safely without duplicating tables or destroying data.
--
-- Cross-user integrity:
--   Composite foreign keys use (id, user_id) unique parent keys so that a
--   record can only reference another record owned by the same user. These are
--   additional database-level protections; server-side ownership checks remain
--   mandatory in application code.

PRAGMA foreign_keys = ON;

-- 1. users ---------------------------------------------------------------
CREATE TABLE IF NOT EXISTS users (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    username      TEXT    NOT NULL UNIQUE
                          CHECK (length(trim(username)) BETWEEN 1 AND 50),
    email         TEXT    NOT NULL UNIQUE
                          CHECK (email LIKE '%_@_%._%'),
    password_hash TEXT    NOT NULL
                          CHECK (length(password_hash) > 0),
    role          TEXT    NOT NULL DEFAULT 'user'
                          CHECK (role IN ('user', 'admin')),
    quota_bytes   INTEGER NOT NULL DEFAULT 0
                          CHECK (quota_bytes >= 0),
    used_bytes    INTEGER NOT NULL DEFAULT 0
                          CHECK (used_bytes >= 0),
    is_active     INTEGER NOT NULL DEFAULT 1
                          CHECK (is_active IN (0, 1)),
    created_at    TEXT    NOT NULL
                          DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    updated_at    TEXT    NOT NULL
                          DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

CREATE INDEX IF NOT EXISTS idx_users_role ON users(role);
CREATE INDEX IF NOT EXISTS idx_users_is_active ON users(is_active);

-- 2. folders -------------------------------------------------------------
CREATE TABLE IF NOT EXISTS folders (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id    INTEGER NOT NULL
                       REFERENCES users(id) ON DELETE CASCADE,
    parent_id  INTEGER,
    name       TEXT    NOT NULL
                       CHECK (length(trim(name)) BETWEEN 1 AND 255),
    created_at TEXT    NOT NULL
                       DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    updated_at TEXT    NOT NULL
                       DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    UNIQUE (id, user_id),
    CHECK (parent_id IS NULL OR parent_id <> id),
    -- A parent folder must belong to the same user as the child folder.
    FOREIGN KEY (parent_id, user_id)
        REFERENCES folders(id, user_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_folders_user_parent ON folders(user_id, parent_id);

-- Root folders: a user may not have two root folders with the same name.
CREATE UNIQUE INDEX IF NOT EXISTS idx_folders_unique_root_name
    ON folders(user_id, name)
    WHERE parent_id IS NULL;

-- Child folders: a user may not have two folders with the same name in the
-- same parent folder.
CREATE UNIQUE INDEX IF NOT EXISTS idx_folders_unique_child_name
    ON folders(user_id, parent_id, name)
    WHERE parent_id IS NOT NULL;

-- 3. files ---------------------------------------------------------------
CREATE TABLE IF NOT EXISTS files (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id           INTEGER NOT NULL
                              REFERENCES users(id) ON DELETE CASCADE,
    folder_id         INTEGER,
    original_filename TEXT    NOT NULL
                              CHECK (length(trim(original_filename)) BETWEEN 1 AND 255),
    stored_filename   TEXT    NOT NULL UNIQUE
                              CHECK (length(trim(stored_filename)) > 0),
    size_bytes        INTEGER NOT NULL DEFAULT 0
                              CHECK (size_bytes >= 0),
    mime_type         TEXT    NOT NULL DEFAULT 'application/octet-stream',
    checksum_sha256   TEXT
                              CHECK (checksum_sha256 IS NULL OR length(checksum_sha256) = 64),
    status            TEXT    NOT NULL DEFAULT 'active'
                              CHECK (status IN ('active', 'deleted')),
    deleted_at        TEXT,
    created_at        TEXT    NOT NULL
                              DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    updated_at        TEXT    NOT NULL
                              DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    UNIQUE (id, user_id),
    CHECK (
        (status = 'active' AND deleted_at IS NULL)
        OR (status = 'deleted' AND deleted_at IS NOT NULL)
    ),
    -- A file may only be placed in a folder owned by the same user. Folder
    -- deletion is NO ACTION: application code must move files to the root
    -- (folder_id = NULL) before deleting a folder so no file is lost.
    FOREIGN KEY (folder_id, user_id)
        REFERENCES folders(id, user_id) ON DELETE NO ACTION
);

CREATE INDEX IF NOT EXISTS idx_files_user_status ON files(user_id, status);
CREATE INDEX IF NOT EXISTS idx_files_folder ON files(folder_id);
CREATE INDEX IF NOT EXISTS idx_files_original_filename ON files(original_filename);

-- 4. file_versions -------------------------------------------------------
CREATE TABLE IF NOT EXISTS file_versions (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    file_id         INTEGER NOT NULL
                            REFERENCES files(id) ON DELETE CASCADE,
    version_number  INTEGER NOT NULL
                            CHECK (version_number >= 1),
    size_bytes      INTEGER NOT NULL
                            CHECK (size_bytes >= 0),
    checksum_sha256 TEXT    NOT NULL
                            CHECK (length(checksum_sha256) = 64),
    backup_path     TEXT    NOT NULL
                            CHECK (length(trim(backup_path)) > 0),
    created_at      TEXT    NOT NULL
                            DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    UNIQUE (file_id, version_number)
);

CREATE INDEX IF NOT EXISTS idx_file_versions_file ON file_versions(file_id);

-- 5. tags ----------------------------------------------------------------
CREATE TABLE IF NOT EXISTS tags (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id    INTEGER NOT NULL
                       REFERENCES users(id) ON DELETE CASCADE,
    name       TEXT    NOT NULL
                       CHECK (length(trim(name)) BETWEEN 1 AND 50),
    created_at TEXT    NOT NULL
                       DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    UNIQUE (user_id, name),
    UNIQUE (id, user_id)
);

CREATE INDEX IF NOT EXISTS idx_tags_user ON tags(user_id);

-- 6. file_tags -----------------------------------------------------------
-- user_id is an ownership column used to enforce that a file and a tag being
-- linked belong to the same user.
CREATE TABLE IF NOT EXISTS file_tags (
    file_id    INTEGER NOT NULL,
    tag_id     INTEGER NOT NULL,
    user_id    INTEGER NOT NULL,
    created_at TEXT    NOT NULL
                       DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    PRIMARY KEY (file_id, tag_id),
    FOREIGN KEY (file_id, user_id)
        REFERENCES files(id, user_id) ON DELETE CASCADE,
    FOREIGN KEY (tag_id, user_id)
        REFERENCES tags(id, user_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_file_tags_tag ON file_tags(tag_id);
CREATE INDEX IF NOT EXISTS idx_file_tags_user ON file_tags(user_id);

-- 7. shares --------------------------------------------------------------
CREATE TABLE IF NOT EXISTS shares (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    file_id    INTEGER NOT NULL,
    user_id    INTEGER NOT NULL
                       REFERENCES users(id) ON DELETE CASCADE,
    token      TEXT    NOT NULL UNIQUE
                       CHECK (length(trim(token)) >= 16),
    expires_at TEXT    NOT NULL,
    revoked    INTEGER NOT NULL DEFAULT 0
                       CHECK (revoked IN (0, 1)),
    created_at TEXT    NOT NULL
                       DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    CHECK (expires_at > created_at),
    -- A share's owner must match the owner of the shared file.
    FOREIGN KEY (file_id, user_id)
        REFERENCES files(id, user_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_shares_file ON shares(file_id);
CREATE INDEX IF NOT EXISTS idx_shares_token ON shares(token);
CREATE INDEX IF NOT EXISTS idx_shares_expires_at ON shares(expires_at);

-- 8. upload_sessions -----------------------------------------------------
CREATE TABLE IF NOT EXISTS upload_sessions (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id          INTEGER NOT NULL
                             REFERENCES users(id) ON DELETE CASCADE,
    file_id          INTEGER,
    folder_id        INTEGER,
    upload_id        TEXT    NOT NULL UNIQUE
                             CHECK (length(trim(upload_id)) > 0),
    original_filename TEXT   NOT NULL
                             CHECK (length(trim(original_filename)) BETWEEN 1 AND 255),
    total_size       INTEGER NOT NULL DEFAULT 0
                             CHECK (total_size >= 0),
    received_bytes   INTEGER NOT NULL DEFAULT 0
                             CHECK (received_bytes >= 0),
    total_chunks     INTEGER NOT NULL DEFAULT 0
                             CHECK (total_chunks >= 0),
    received_chunks  INTEGER NOT NULL DEFAULT 0
                             CHECK (received_chunks >= 0),
    status           TEXT    NOT NULL DEFAULT 'in_progress'
                             CHECK (status IN ('in_progress', 'completed', 'aborted')),
    created_at       TEXT    NOT NULL
                             DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    updated_at       TEXT    NOT NULL
                             DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    CHECK (received_bytes <= total_size),
    CHECK (received_chunks <= total_chunks),
    -- An upload session may only reference files and folders owned by the
    -- same user. NULL file_id/folder_id means "not yet assigned".
    FOREIGN KEY (file_id, user_id)
        REFERENCES files(id, user_id) ON DELETE CASCADE,
    FOREIGN KEY (folder_id, user_id)
        REFERENCES folders(id, user_id) ON DELETE NO ACTION
);

CREATE INDEX IF NOT EXISTS idx_upload_sessions_user_status
    ON upload_sessions(user_id, status);

-- 9. backup_checks -------------------------------------------------------
CREATE TABLE IF NOT EXISTS backup_checks (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    file_version_id   INTEGER NOT NULL
                              REFERENCES file_versions(id) ON DELETE CASCADE,
    checked_by        INTEGER REFERENCES users(id) ON DELETE SET NULL,
    status            TEXT    NOT NULL
                              CHECK (status IN ('ok', 'damaged', 'missing')),
    expected_checksum TEXT    NOT NULL
                              CHECK (length(expected_checksum) = 64),
    actual_checksum   TEXT
                              CHECK (actual_checksum IS NULL OR length(actual_checksum) = 64),
    details           TEXT,
    checked_at        TEXT    NOT NULL
                              DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

CREATE INDEX IF NOT EXISTS idx_backup_checks_version
    ON backup_checks(file_version_id);
CREATE INDEX IF NOT EXISTS idx_backup_checks_status
    ON backup_checks(status);

-- 10. activity_logs ------------------------------------------------------
CREATE TABLE IF NOT EXISTS activity_logs (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id     INTEGER REFERENCES users(id) ON DELETE SET NULL,
    action      TEXT    NOT NULL
                        CHECK (length(trim(action)) > 0),
    entity_type TEXT,
    entity_id   INTEGER,
    details     TEXT,
    ip_address  TEXT,
    created_at  TEXT    NOT NULL
                        DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

CREATE INDEX IF NOT EXISTS idx_activity_logs_user ON activity_logs(user_id);
CREATE INDEX IF NOT EXISTS idx_activity_logs_action ON activity_logs(action);
CREATE INDEX IF NOT EXISTS idx_activity_logs_created_at ON activity_logs(created_at);

-- 11. login_attempts -----------------------------------------------------
CREATE TABLE IF NOT EXISTS login_attempts (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id            INTEGER REFERENCES users(id) ON DELETE SET NULL,
    username_attempted TEXT    NOT NULL
                               CHECK (length(trim(username_attempted)) > 0),
    success            INTEGER NOT NULL
                               CHECK (success IN (0, 1)),
    ip_address         TEXT,
    user_agent         TEXT,
    attempted_at       TEXT    NOT NULL
                               DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

CREATE INDEX IF NOT EXISTS idx_login_attempts_username
    ON login_attempts(username_attempted);
CREATE INDEX IF NOT EXISTS idx_login_attempts_attempted_at
    ON login_attempts(attempted_at);

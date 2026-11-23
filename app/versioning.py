"""Shared versioned-backup creation for completed uploads (Stage 5).

Each newly uploaded file receives an initial verified backup snapshot. The
helper runs inside the caller's SQLite transaction; callers remove the
returned expired backup files only after that transaction commits.
"""

import hashlib
import os
import uuid
from pathlib import Path

from flask import current_app

CHUNK_SIZE = 64 * 1024
MAX_RETAINED_VERSIONS = 5


def _safe_expired_path(raw_path, root, expected_parent):
    relative = Path(raw_path)
    if relative.is_absolute() or ".." in relative.parts:
        return None
    candidate = (root / relative).resolve()
    if candidate.parent != expected_parent or root not in candidate.parents:
        return None
    return candidate


def create_initial_backup(connection, user_id, file_id, source_path):
    """Back up a newly stored file, insert its version row and prune old rows.

    Returns a pair of the newly created backup path and expired backup paths.
    Expired files must only be unlinked after the surrounding DB transaction
    commits successfully.
    """
    root = Path(current_app.config["BACKUP_DIR"]).resolve()
    directory = (root / str(user_id) / str(file_id)).resolve()
    if root not in directory.parents:
        raise OSError("Backup directory escaped the configured backup root.")
    directory.mkdir(parents=True, exist_ok=True)

    destination = directory / uuid.uuid4().hex
    digest = hashlib.sha256()
    size = 0

    try:
        with Path(source_path).open("rb") as source, destination.open("xb") as target:
            while True:
                chunk = source.read(CHUNK_SIZE)
                if not chunk:
                    break
                target.write(chunk)
                digest.update(chunk)
                size += len(chunk)
            target.flush()
            os.fsync(target.fileno())

        checksum = digest.hexdigest()
        next_number = connection.execute(
            "SELECT COALESCE(MAX(version_number), 0) + 1 "
            "FROM file_versions WHERE file_id = ?",
            (file_id,),
        ).fetchone()[0]
        relative_path = destination.relative_to(root).as_posix()
        connection.execute(
            "INSERT INTO file_versions "
            "(file_id, version_number, size_bytes, checksum_sha256, backup_path) "
            "VALUES (?, ?, ?, ?, ?)",
            (file_id, next_number, size, checksum, relative_path),
        )

        expired_rows = connection.execute(
            "SELECT id, backup_path FROM file_versions WHERE file_id = ? "
            "ORDER BY version_number DESC, id DESC LIMIT -1 OFFSET ?",
            (file_id, MAX_RETAINED_VERSIONS),
        ).fetchall()
        expired_paths = [
            path for row in expired_rows
            if (path := _safe_expired_path(row["backup_path"], root, directory))
            is not None
        ]
        expired_ids = [row["id"] for row in expired_rows]
        if expired_ids:
            placeholders = ",".join("?" for _ in expired_ids)
            connection.execute(
                f"DELETE FROM file_versions WHERE id IN ({placeholders})",
                expired_ids,
            )
        return destination, expired_paths
    except Exception:
        try:
            destination.unlink(missing_ok=True)
        except OSError:
            pass
        raise


def remove_expired_backups(paths):
    """Delete pruned backup files after their database rows have committed."""
    for path in paths:
        try:
            Path(path).unlink(missing_ok=True)
        except OSError:
            current_app.logger.warning(
                "Could not remove an expired version backup: %s", path
            )

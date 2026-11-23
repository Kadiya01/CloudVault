"""Version history and verified backup restoration (Stage 5).

The current file remains in storage/<user_id>/; historical snapshots are
stored under backup/<user_id>/<file_id>/. Backup paths are always resolved
beneath the configured backup root and are never returned to clients.
"""

import hashlib
import json
import os
import re
import shutil
import uuid
from pathlib import Path

from flask import Blueprint, abort, current_app, g, jsonify, request

from .auth import login_required

bp = Blueprint("versions", __name__)
CHUNK_SIZE = 64 * 1024
STORED_NAME_RE = re.compile(r"^[0-9a-f]{32}$")
MAX_RETAINED_VERSIONS = 5


def _hash_file(path):
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as stream:
        while True:
            chunk = stream.read(CHUNK_SIZE)
            if not chunk:
                break
            digest.update(chunk)
            size += len(chunk)
    return digest.hexdigest(), size


def _copy_file(source, destination):
    """Copy in bounded chunks and return the copied file's SHA-256 and size."""
    digest = hashlib.sha256()
    size = 0
    destination.parent.mkdir(parents=True, exist_ok=True)
    with source.open("rb") as src, destination.open("xb") as dst:
        while True:
            chunk = src.read(CHUNK_SIZE)
            if not chunk:
                break
            dst.write(chunk)
            digest.update(chunk)
            size += len(chunk)
        dst.flush()
        os.fsync(dst.fileno())
    shutil.copystat(source, destination, follow_symlinks=True)
    return digest.hexdigest(), size


def _owned_file_or_404(connection, file_id, user_id):
    row = connection.execute(
        "SELECT * FROM files WHERE id = ? AND user_id = ?",
        (file_id, user_id),
    ).fetchone()
    if row is None:
        abort(404)
    return row


def _current_path(file_row):
    stored_name = file_row["stored_filename"] or ""
    if not STORED_NAME_RE.fullmatch(stored_name):
        abort(404)
    root = Path(current_app.config["STORAGE_DIR"]).resolve()
    user_root = (root / str(file_row["user_id"])).resolve()
    path = (user_root / stored_name).resolve()
    if path.parent != user_root:
        abort(404)
    return path


def _backup_directory(user_id, file_id):
    root = Path(current_app.config["BACKUP_DIR"]).resolve()
    directory = (root / str(user_id) / str(file_id)).resolve()
    if root not in directory.parents:
        abort(404)
    directory.mkdir(parents=True, exist_ok=True)
    return root, directory


def _backup_path_for_row(version, user_id, file_id):
    """Resolve a DB backup path and reject absolute paths or traversal."""
    raw = version["backup_path"]
    relative = Path(raw)
    if relative.is_absolute() or ".." in relative.parts:
        return None
    root = Path(current_app.config["BACKUP_DIR"]).resolve()
    expected_parent = (root / str(user_id) / str(file_id)).resolve()
    path = (root / relative).resolve()
    if path.parent != expected_parent or root not in path.parents:
        return None
    return path


def _record_backup_check(connection, version_id, user_id, status,
                         expected, actual, details=None):
    connection.execute(
        "INSERT INTO backup_checks "
        "(file_version_id, checked_by, status, expected_checksum, "
        "actual_checksum, details) VALUES (?, ?, ?, ?, ?, ?)",
        (version_id, user_id, status, expected, actual, details),
    )


def _verify_version(connection, version, user_id, file_id, record=False):
    path = _backup_path_for_row(version, user_id, file_id)
    if path is None or not path.is_file():
        status, actual, details = "missing", None, "Backup file is missing."
    else:
        try:
            actual, _ = _hash_file(path)
            status = "ok" if actual == version["checksum_sha256"] else "damaged"
            details = None if status == "ok" else "SHA-256 checksum mismatch."
        except OSError:
            status, actual, details = "missing", None, "Backup file could not be read."
    if record:
        _record_backup_check(
            connection, version["id"], user_id, status,
            version["checksum_sha256"], actual, details,
        )
    return status


def _latest_check(connection, version_id):
    row = connection.execute(
        "SELECT status FROM backup_checks WHERE file_version_id = ? "
        "ORDER BY checked_at DESC, id DESC LIMIT 1",
        (version_id,),
    ).fetchone()
    return row["status"] if row else None


@bp.get("/api/files/<int:file_id>/versions")
@login_required
def list_versions(file_id):
    connection = database.get_db()
    user_id = g.user["id"]
    file_row = _owned_file_or_404(connection, file_id, user_id)
    current_path = _current_path(file_row)

    current_checksum_status = "missing"
    if current_path.is_file():
        try:
            actual, _ = _hash_file(current_path)
            current_checksum_status = (
                "ok" if actual == file_row["checksum_sha256"] else "damaged"
            ) if file_row["checksum_sha256"] else "unverified"
        except OSError:
            current_checksum_status = "missing"

    rows = connection.execute(
        "SELECT id, version_number, size_bytes, checksum_sha256, backup_path, "
        "created_at FROM file_versions WHERE file_id = ? "
        "ORDER BY version_number DESC, id DESC LIMIT ?",
        (file_id, MAX_RETAINED_VERSIONS),
    ).fetchall()

    latest_number = rows[0]["version_number"] if rows else 0
    restored_version_ids = set()
    restore_logs = connection.execute(
        "SELECT details FROM activity_logs WHERE user_id = ? "
        "AND action = 'file_version_restored' AND entity_type = 'file' "
        "AND entity_id = ?",
        (user_id, file_id),
    ).fetchall()
    for log_row in restore_logs:
        try:
            details = json.loads(log_row["details"] or "{}")
            restored_version_ids.add(int(details["restored_version_id"]))
        except (TypeError, ValueError, KeyError, json.JSONDecodeError):
            continue

    versions = [{
        "id": None,
        "version_number": latest_number + 1,
        "original_filename": file_row["original_filename"],
        "size_bytes": file_row["size_bytes"],
        "created_at": file_row["updated_at"],
        "is_current": True,
        "is_restored": False,
        "backup_status": "active" if current_path.is_file() else "missing",
        "checksum_status": current_checksum_status,
    }]

    for row in rows:
        backup_status = _verify_version(
            connection, row, user_id, file_id, record=False
        )
        versions.append({
            "id": row["id"],
            "version_number": row["version_number"],
            "original_filename": file_row["original_filename"],
            "size_bytes": row["size_bytes"],
            "created_at": row["created_at"],
            "is_current": False,
            "is_restored": row["id"] in restored_version_ids,
            "backup_status": "backed_up" if backup_status == "ok" else backup_status,
            "checksum_status": backup_status,
        })

    return jsonify({
        "file": {
            "id": file_row["id"],
            "original_filename": file_row["original_filename"],
            "status": file_row["status"],
        },
        "versions": versions,
    })


@bp.post("/api/files/<int:file_id>/versions/<int:version_id>/restore")
@login_required
def restore_version(file_id, version_id):
    connection = database.get_db()
    user_id = g.user["id"]
    file_row = _owned_file_or_404(connection, file_id, user_id)
    if file_row["status"] != "active":
        return jsonify({"error": "Deleted files cannot be restored from version history."}), 400

    selected = connection.execute(
        "SELECT * FROM file_versions WHERE id = ? AND file_id = ?",
        (version_id, file_id),
    ).fetchone()
    if selected is None:
        return jsonify({"error": "The requested file version was not found."}), 404

    current_path = _current_path(file_row)
    selected_path = _backup_path_for_row(selected, user_id, file_id)
    if selected_path is None or not selected_path.is_file():
        _record_backup_check(
            connection, selected["id"], user_id, "missing",
            selected["checksum_sha256"], None, "Backup file is missing.",
        )
        connection.commit()
        return jsonify({"error": "This backup is missing and cannot be restored."}), 400

    try:
        selected_checksum, _ = _hash_file(selected_path)
    except OSError:
        selected_checksum, selected_size = None, None
    if selected_checksum != selected["checksum_sha256"]:
        _record_backup_check(
            connection, selected["id"], user_id, "damaged",
            selected["checksum_sha256"], selected_checksum,
            "SHA-256 checksum mismatch.",
        )
        connection.commit()
        return jsonify({"error": "Backup verification failed. The file was not changed."}), 400

    if not current_path.is_file():
        return jsonify({"error": "The current file is missing; restoration was stopped."}), 400

    root, backup_dir = _backup_directory(user_id, file_id)
    backup_name = uuid.uuid4().hex
    snapshot_path = backup_dir / backup_name
    restore_temp = current_path.with_name(current_path.name + "." + uuid.uuid4().hex + ".restore")

    snapshot_created = False
    replacement_done = False
    try:
        connection.execute("BEGIN IMMEDIATE")
        # Re-read under the write lock to avoid racing another restore.
        fresh_version = connection.execute(
            "SELECT * FROM file_versions WHERE id = ? AND file_id = ?",
            (version_id, file_id),
        ).fetchone()
        if fresh_version is None:
            connection.rollback()
            return jsonify({"error": "The requested file version no longer exists."}), 404

        # Preserve the exact current content before replacing it.
        current_checksum, current_size = _copy_file(current_path, snapshot_path)
        snapshot_created = True
        relative_backup_path = snapshot_path.relative_to(root).as_posix()

        next_number = connection.execute(
            "SELECT COALESCE(MAX(version_number), 0) + 1 "
            "FROM file_versions WHERE file_id = ?",
            (file_id,),
        ).fetchone()[0]
        connection.execute(
            "INSERT INTO file_versions "
            "(file_id, version_number, size_bytes, checksum_sha256, backup_path) "
            "VALUES (?, ?, ?, ?, ?)",
            (file_id, next_number, current_size, current_checksum, relative_backup_path),
        )

        # Stage restored bytes beside the live file, then atomically replace it.
        restored_checksum, restored_size = _copy_file(selected_path, restore_temp)
        if restored_checksum != fresh_version["checksum_sha256"]:
            raise OSError("Backup checksum changed during restoration.")
        os.replace(restore_temp, current_path)
        replacement_done = True

        connection.execute(
            "UPDATE files SET size_bytes = ?, checksum_sha256 = ?, "
            "updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now') "
            "WHERE id = ? AND user_id = ?",
            (restored_size, restored_checksum, file_id, user_id),
        )
        connection.execute(
            "UPDATE users SET used_bytes = ("
            "SELECT COALESCE(SUM(size_bytes), 0) FROM files WHERE user_id = ?"
            ") WHERE id = ?",
            (user_id, user_id),
        )
        _record_backup_check(
            connection, fresh_version["id"], user_id, "ok",
            fresh_version["checksum_sha256"], restored_checksum,
            "Checksum verified immediately before restore.",
        )
        connection.execute(
            "INSERT INTO activity_logs "
            "(user_id, action, entity_type, entity_id, details, ip_address) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (
                user_id, "file_version_restored", "file", file_id,
                json.dumps({
                    "restored_version_id": version_id,
                    "restored_version_number": fresh_version["version_number"],
                    "preserved_version_number": next_number,
                }, separators=(",", ":")),
                request.remote_addr,
            ),
        )

        # Keep the five newest historical snapshots; delete old files only
        # after the database transaction commits successfully.
        old_rows = connection.execute(
            "SELECT id, backup_path FROM file_versions "
            "WHERE file_id = ? AND id <> ? "
            "ORDER BY version_number DESC, id DESC LIMIT -1 OFFSET ?",
            (file_id, version_id, MAX_RETAINED_VERSIONS - 1),
        ).fetchall()
        old_paths = [
            _backup_path_for_row(row, user_id, file_id) for row in old_rows
        ]
        old_ids = [row["id"] for row in old_rows]
        if old_ids:
            placeholders = ",".join("?" for _ in old_ids)
            connection.execute(
                f"DELETE FROM file_versions WHERE id IN ({placeholders})",
                old_ids,
            )

        connection.commit()
        for old_path in old_paths:
            if old_path is not None:
                try:
                    old_path.unlink(missing_ok=True)
                except OSError:
                    current_app.logger.warning(
                        "Could not remove expired backup file for file %s", file_id
                    )

        return jsonify({
            "success": True,
            "message": (
                f"Version v{fresh_version['version_number']} restored successfully "
                "as the current file."
            ),
            "new_version_number": next_number + 1,
            "file_id": file_id,
        })
    except Exception:
        connection.rollback()
        # If the DB transaction failed after replacement, restore the original
        # bytes from the snapshot created immediately before the replacement.
        if replacement_done and snapshot_created and snapshot_path.is_file():
            try:
                os.replace(snapshot_path, current_path)
            except OSError:
                current_app.logger.exception(
                    "CRITICAL: failed to roll back file bytes after restore error"
                )
        raise
    finally:
        try:
            restore_temp.unlink(missing_ok=True)
        except OSError:
            pass
        if snapshot_created and not replacement_done:
            try:
                snapshot_path.unlink(missing_ok=True)
            except OSError:
                pass

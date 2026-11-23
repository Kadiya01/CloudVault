"""Files-only recycle bin API (Stage 6, Part 1).

Deleted files are retained for 30 days. Physical file and version-backup
objects are quarantined by atomic rename before their database rows are
removed; failed database work restores the quarantined objects.
"""

import os
import re
import uuid
from datetime import datetime, timedelta, timezone
from functools import wraps
from pathlib import Path

from flask import Blueprint, current_app, g, jsonify, render_template, request

from . import database
from .auth import login_required

bp = Blueprint("recycle_bin", __name__)
RETENTION_DAYS = 30
STORED_NAME_RE = re.compile(r"^[0-9a-f]{32}$")


def api_login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if g.get("user") is None:
            return jsonify(error="Authentication required."), 401
        return view(*args, **kwargs)
    return wrapped


def _utcnow():
    return datetime.now(timezone.utc)


def _parse_utc(value):
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    except (AttributeError, TypeError, ValueError):
        return None


def _expired(deleted_at, now=None):
    deleted = _parse_utc(deleted_at)
    return deleted is not None and (now or _utcnow()) >= deleted + timedelta(days=RETENTION_DAYS)


def _owned_deleted_file(connection, file_id, user_id):
    row = connection.execute(
        "SELECT * FROM files WHERE id = ? AND user_id = ? AND status = 'deleted'",
        (file_id, user_id),
    ).fetchone()
    return row


def _storage_path(row):
    stored_name = row["stored_filename"] or ""
    if not STORED_NAME_RE.fullmatch(stored_name):
        raise ValueError("Stored file path is invalid.")
    root = Path(current_app.config["STORAGE_DIR"]).resolve()
    user_dir = (root / str(row["user_id"])).resolve()
    candidate = user_dir / stored_name
    if candidate.is_symlink():
        raise ValueError("Stored file path must not be a symbolic link.")
    path = candidate.resolve()
    if user_dir.parent != root or path.parent != user_dir:
        raise ValueError("Stored file path is outside its owner directory.")
    return path


def _backup_paths(connection, row):
    root = Path(current_app.config["BACKUP_DIR"]).resolve()
    expected_dir = (root / str(row["user_id"]) / str(row["id"])).resolve()
    if root not in expected_dir.parents:
        raise ValueError("Backup directory is invalid.")
    result = []
    versions = connection.execute(
        "SELECT backup_path FROM file_versions WHERE file_id = ?",
        (row["id"],),
    ).fetchall()
    for version in versions:
        raw = version["backup_path"] or ""
        relative = Path(raw)
        if relative.is_absolute() or ".." in relative.parts:
            raise ValueError("A backup path is invalid.")
        candidate = root / relative
        if candidate.is_symlink():
            raise ValueError("Backup path must not be a symbolic link.")
        path = candidate.resolve()
        if path.parent != expected_dir or root not in path.parents:
            raise ValueError("A backup path is outside its file backup directory.")
        result.append(path)
    return expected_dir, list(dict.fromkeys(result))


def _quarantine(paths):
    """Atomically move existing files aside; return (original, quarantine) pairs."""
    moved = []
    try:
        for path in paths:
            if path.is_symlink():
                raise ValueError("Refusing to permanently delete a symbolic link.")
            if not path.exists():
                continue
            if not path.is_file():
                raise ValueError("A stored object is not a regular file.")
            quarantined = path.with_name(path.name + ".deleting-" + uuid.uuid4().hex)
            os.replace(path, quarantined)
            moved.append((path, quarantined))
        return moved
    except Exception:
        _restore_quarantine(moved)
        raise


def _restore_quarantine(moved):
    for original, quarantined in reversed(moved):
        try:
            if quarantined.exists():
                os.replace(quarantined, original)
        except OSError:
            current_app.logger.exception(
                "Could not restore quarantined CloudVault object: %s", original
            )


def _finish_quarantine(moved):
    for _, quarantined in moved:
        try:
            quarantined.unlink(missing_ok=True)
        except OSError:
            current_app.logger.exception(
                "Could not remove permanently deleted CloudVault object: %s",
                quarantined,
            )


def _delete_one(connection, row, user_id):
    """Delete one file and its DB-cascaded metadata, preserving rollback safety."""
    moved = []
    backup_dir = None
    try:
        # Lock and re-check the row before touching disk. Without this ordering,
        # a concurrent restore could reactivate the file after the initial
        # lookup but before quarantine moves its physical file.
        connection.execute("BEGIN IMMEDIATE")
        fresh = connection.execute(
            "SELECT id FROM files WHERE id = ? AND user_id = ? AND status = 'deleted'",
            (row["id"], user_id),
        ).fetchone()
        if fresh is None:
            connection.rollback()
            return False

        current_path = _storage_path(row)
        backup_dir, backups = _backup_paths(connection, row)
        moved = _quarantine([current_path, *backups])

        connection.execute(
            "INSERT INTO activity_logs "
            "(user_id, action, entity_type, entity_id, details, ip_address) "
            "VALUES (?, ?, 'file', ?, ?, ?)",
            (
                user_id,
                "file_permanently_deleted",
                row["id"],
                '{"source":"recycle_bin"}',
                request.remote_addr,
            ),
        )
        connection.execute(
            "DELETE FROM files WHERE id = ? AND user_id = ? AND status = 'deleted'",
            (row["id"], user_id),
        )
        connection.execute(
            "UPDATE users SET used_bytes = ("
            "SELECT COALESCE(SUM(size_bytes), 0) FROM files WHERE user_id = ?"
            ") WHERE id = ?",
            (user_id, user_id),
        )
        connection.commit()
    except Exception:
        connection.rollback()
        _restore_quarantine(moved)
        raise

    _finish_quarantine(moved)
    try:
        backup_dir.rmdir()
        backup_dir.parent.rmdir()
    except OSError:
        pass
    return True


@bp.get("/recycle-bin")
@login_required
def recycle_bin_page():
    return render_template("recycle_bin.html")


@bp.get("/api/recycle-bin")
@api_login_required
def list_deleted_files():
    connection = database.get_db()
    user_id = g.user["id"]
    rows = connection.execute(
        "SELECT id, original_filename, size_bytes, mime_type, deleted_at "
        "FROM files WHERE user_id = ? AND status = 'deleted' "
        "ORDER BY deleted_at DESC, id DESC",
        (user_id,),
    ).fetchall()
    now = _utcnow()
    files = []
    for row in rows:
        deleted = _parse_utc(row["deleted_at"])
        expires = deleted + timedelta(days=RETENTION_DAYS) if deleted else None
        files.append({
            "id": row["id"],
            "original_filename": row["original_filename"],
            "size_bytes": row["size_bytes"],
            "mime_type": row["mime_type"],
            "deleted_at": row["deleted_at"],
            "expires_at": expires.isoformat().replace("+00:00", "Z") if expires else None,
            "is_expired": _expired(row["deleted_at"], now),
        })
    return jsonify(files=files, retention_days=RETENTION_DAYS)


@bp.post("/api/recycle-bin/files/<int:file_id>/restore")
@api_login_required
def restore_deleted_file(file_id):
    connection = database.get_db()
    user_id = g.user["id"]
    row = _owned_deleted_file(connection, file_id, user_id)
    if row is None:
        return jsonify(error="Deleted file not found."), 404
    try:
        path = _storage_path(row)
    except ValueError:
        current_app.logger.exception("Invalid storage path for deleted file %s", file_id)
        return jsonify(error="The stored file path is invalid; restoration was stopped."), 409
    if not path.is_file():
        return jsonify(error="The stored file is missing; restoration was stopped."), 409

    try:
        connection.execute("BEGIN IMMEDIATE")
        cursor = connection.execute(
            "UPDATE files SET status = 'active', deleted_at = NULL, "
            "updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now') "
            "WHERE id = ? AND user_id = ? AND status = 'deleted'",
            (file_id, user_id),
        )
        if cursor.rowcount != 1:
            connection.rollback()
            return jsonify(error="Deleted file not found."), 404
        connection.execute(
            "INSERT INTO activity_logs "
            "(user_id, action, entity_type, entity_id, details, ip_address) "
            "VALUES (?, ?, 'file', ?, ?, ?)",
            (user_id, "file_restored_from_recycle_bin", file_id,
             "{}", request.remote_addr),
        )
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    return jsonify(success=True, message="File restored successfully.", file_id=file_id)


@bp.post("/api/recycle-bin/files/<int:file_id>/delete")
@api_login_required
def permanently_delete_file(file_id):
    connection = database.get_db()
    user_id = g.user["id"]
    row = _owned_deleted_file(connection, file_id, user_id)
    if row is None:
        return jsonify(error="Deleted file not found."), 404
    try:
        deleted = _delete_one(connection, row, user_id)
    except (OSError, ValueError) as exc:
        current_app.logger.exception("Permanent deletion failed for file %s", file_id)
        return jsonify(error="Permanent deletion could not be completed safely."), 500
    if not deleted:
        return jsonify(error="Deleted file not found."), 404
    return jsonify(success=True, message="File permanently deleted.", file_id=file_id)


@bp.post("/api/recycle-bin/purge")
@api_login_required
def purge_expired_files():
    connection = database.get_db()
    user_id = g.user["id"]
    rows = connection.execute(
        "SELECT * FROM files WHERE user_id = ? AND status = 'deleted' "
        "ORDER BY deleted_at ASC, id ASC",
        (user_id,),
    ).fetchall()
    now = _utcnow()
    eligible = [row for row in rows if _expired(row["deleted_at"], now)]
    deleted_count = 0
    failed_count = 0
    for row in eligible:
        try:
            if _delete_one(connection, row, user_id):
                deleted_count += 1
        except (OSError, ValueError):
            failed_count += 1
            current_app.logger.exception(
                "Recycle-bin purge failed for file %s", row["id"]
            )
    return jsonify(
        success=(failed_count == 0),
        deleted_count=deleted_count,
        failed_count=failed_count,
        message=(
            f"Purged {deleted_count} expired file(s)."
            if failed_count == 0
            else f"Purged {deleted_count} file(s); {failed_count} could not be safely deleted."
        ),
    ), (200 if failed_count == 0 else 207)

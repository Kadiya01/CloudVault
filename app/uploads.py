"""Resumable chunked uploads (Stage 4).

Uploads are transferred in fixed 2 MB chunks, tracked by the approved
``upload_sessions`` table and held under ``temporary_uploads/<upload_id>/``.
A session expires 24 hours after creation; the expiry is derived from
``created_at`` because the approved schema does not include an expiry column.
Only a fully assembled, revalidated file is moved into permanent storage.
"""

import hashlib
import mimetypes
import os
import re
import shutil
import sqlite3
import uuid
from datetime import datetime, timedelta, timezone
from functools import wraps
from pathlib import Path

from flask import Blueprint, current_app, g, jsonify, request, url_for
from werkzeug.utils import secure_filename

from . import database
from .files import (
    _allowed_filename,
    _effective_quota,
    _recalculate_used_bytes,
    _user_storage_dir,
)
from .versioning import create_initial_backup, remove_expired_backups

bp = Blueprint("uploads", __name__, url_prefix="/api/uploads")

CHUNK_SIZE = 2 * 1024 * 1024  # 2 MB, fixed by the Stage 4 specification.
READ_SIZE = 64 * 1024  # 64 KB read/hash chunks.
SESSION_TTL = timedelta(hours=24)
SESSION_ID_RE = re.compile(r"^[0-9a-f]{32}$")


class ApiError(Exception):
    """A controlled JSON API failure."""

    def __init__(self, message, status=400):
        super().__init__(message)
        self.message = message
        self.status = status


@bp.errorhandler(ApiError)
def handle_api_error(error):
    return jsonify(error=error.message), error.status


def api_login_required(view):
    """Require authentication and return JSON instead of a login redirect."""

    @wraps(view)
    def wrapped(*args, **kwargs):
        if g.get("user") is None:
            return jsonify(error="Authentication required."), 401
        return view(*args, **kwargs)

    return wrapped


# ---------------------------------------------------------------------------
# Time and path helpers
# ---------------------------------------------------------------------------

def _utcnow():
    return datetime.now(timezone.utc)


def _to_iso(moment):
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def _from_iso(value):
    try:
        return datetime.strptime(value, "%Y-%m-%dT%H:%M:%S.%fZ").replace(
            tzinfo=timezone.utc
        )
    except (TypeError, ValueError):
        return None


def _expires_at(row):
    created = _from_iso(row["created_at"])
    return None if created is None else created + SESSION_TTL


def _is_expired(row, now):
    expires = _expires_at(row)
    return expires is None or now >= expires


def _expires_iso(row):
    expires = _expires_at(row)
    return _to_iso(expires) if expires is not None else row["created_at"]


def _temporary_root():
    root = Path(current_app.config["TEMPORARY_UPLOADS_DIR"]).resolve()
    root.mkdir(parents=True, exist_ok=True)
    return root


def _session_dir(upload_id):
    """Return a safe session directory path, rejecting traversal."""
    if not SESSION_ID_RE.fullmatch(upload_id or ""):
        raise ApiError("Upload session not found.", 404)
    root = _temporary_root()
    path = (root / upload_id).resolve()
    if path.parent != root:
        raise ApiError("Upload session not found.", 404)
    return path


def _cleanup_session_dir(root, upload_id):
    """Best-effort safe path for CLI cleanup; returns None when invalid."""
    if not SESSION_ID_RE.fullmatch(upload_id or ""):
        return None
    root = Path(root).resolve()
    path = (root / upload_id).resolve()
    return path if path.parent == root else None


def _chunk_path(session_dir, index):
    path = (session_dir / f"{index}.chunk").resolve()
    if path.parent != session_dir.resolve():
        raise ApiError("Invalid chunk location.", 400)
    return path


def _expected_chunk_size(row, index):
    total_size = int(row["total_size"])
    total_chunks = int(row["total_chunks"])
    if index < 0 or index >= total_chunks:
        raise ApiError("Invalid chunk number.", 400)
    if index == total_chunks - 1:
        return total_size - CHUNK_SIZE * (total_chunks - 1)
    return CHUNK_SIZE


def _scan_received(session_dir, total_chunks):
    """Return (received_indices, received_bytes) from complete chunk files."""
    received = []
    received_bytes = 0
    if not session_dir.is_dir():
        return received, received_bytes
    for index in range(total_chunks):
        path = _chunk_path(session_dir, index)
        try:
            if path.is_file():
                received.append(index)
                received_bytes += path.stat().st_size
        except OSError:
            continue
    return received, received_bytes


def _missing_chunks(received, total_chunks):
    present = set(received)
    return [index for index in range(total_chunks) if index not in present]


def _load_session(connection, upload_id, user_id):
    if not SESSION_ID_RE.fullmatch(upload_id or ""):
        raise ApiError("Upload session not found.", 404)
    row = connection.execute(
        "SELECT * FROM upload_sessions WHERE upload_id = ? AND user_id = ?",
        (upload_id, user_id),
    ).fetchone()
    if row is None:
        raise ApiError("Upload session not found.", 404)
    return row


def _assert_active(row, now):
    if row["status"] == "completed":
        raise ApiError("Upload session has already been completed.", 409)
    if row["status"] != "in_progress":
        raise ApiError("Upload session is no longer active.", 410)
    if _is_expired(row, now):
        raise ApiError("Upload session has expired.", 410)


def _folder_belongs_to_user(connection, folder_id, user_id):
    if not folder_id:
        return True
    row = connection.execute(
        "SELECT 1 FROM folders WHERE id = ? AND user_id = ?",
        (folder_id, user_id),
    ).fetchone()
    return row is not None


# ---------------------------------------------------------------------------
# POST /api/uploads/start
# ---------------------------------------------------------------------------

@bp.post("/start")
@api_login_required
def start_upload():
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        raise ApiError("Expected a JSON request body.")

    filename = payload.get("filename")
    if not isinstance(filename, str):
        raise ApiError("A filename is required.")
    safe_name = secure_filename(filename)
    if not _allowed_filename(safe_name):
        raise ApiError("That file type is not allowed.", 400)

    size = payload.get("size")
    if isinstance(size, bool) or not isinstance(size, int):
        raise ApiError("File size must be an integer number of bytes.")
    if size <= 0:
        raise ApiError("Empty files cannot be uploaded.")
    max_bytes = int(current_app.config["MAX_CONTENT_LENGTH"])
    if size > max_bytes:
        raise ApiError("The file exceeds the maximum allowed upload size.", 413)

    folder_value = payload.get("folder_id")
    folder_id = None
    if folder_value not in (None, ""):
        if isinstance(folder_value, bool):
            raise ApiError("Invalid folder identifier.")
        try:
            folder_id = int(folder_value)
        except (TypeError, ValueError):
            raise ApiError("Invalid folder identifier.")
        if folder_id <= 0:
            raise ApiError("Invalid folder identifier.")

    user_id = g.user["id"]
    connection = database.get_db()

    if not _folder_belongs_to_user(connection, folder_id, user_id):
        raise ApiError("Destination folder not found.", 404)

    user_row = connection.execute(
        "SELECT used_bytes, quota_bytes FROM users WHERE id = ?", (user_id,)
    ).fetchone()
    if user_row["used_bytes"] + size > _effective_quota(user_row):
        raise ApiError("This upload would exceed your storage quota.", 409)

    upload_id = uuid.uuid4().hex
    total_chunks = (size + CHUNK_SIZE - 1) // CHUNK_SIZE
    now = _utcnow()
    timestamp = _to_iso(now)
    session_dir = _session_dir(upload_id)
    created_dir = False

    try:
        session_dir.mkdir(parents=True, exist_ok=False)
        created_dir = True
        connection.execute(
            "INSERT INTO upload_sessions "
            "(user_id, folder_id, upload_id, original_filename, total_size, "
            "total_chunks, status, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, 'in_progress', ?, ?)",
            (
                user_id,
                folder_id,
                upload_id,
                safe_name,
                size,
                total_chunks,
                timestamp,
                timestamp,
            ),
        )
        connection.commit()
    except sqlite3.Error:
        connection.rollback()
        if created_dir:
            shutil.rmtree(session_dir, ignore_errors=True)
        raise ApiError("Could not start the upload session.", 500)
    except OSError:
        if created_dir:
            shutil.rmtree(session_dir, ignore_errors=True)
        raise ApiError("Could not prepare temporary upload storage.", 500)

    return jsonify(
        upload_id=upload_id,
        original_filename=safe_name,
        total_size=size,
        chunk_size=CHUNK_SIZE,
        total_chunks=total_chunks,
        folder_id=folder_id,
        status="in_progress",
        created_at=timestamp,
        expires_at=_to_iso(now + SESSION_TTL),
    ), 201


# ---------------------------------------------------------------------------
# PUT /api/uploads/<id>/chunk/<n>
# ---------------------------------------------------------------------------

@bp.put("/<upload_id>/chunk/<int:chunk_index>")
@api_login_required
def upload_chunk(upload_id, chunk_index):
    user_id = g.user["id"]
    connection = database.get_db()
    row = _load_session(connection, upload_id, user_id)
    now = _utcnow()
    _assert_active(row, now)

    expected_size = _expected_chunk_size(row, chunk_index)
    declared_length = request.content_length
    if declared_length is not None and declared_length != expected_size:
        raise ApiError(
            f"Chunk {chunk_index} must be exactly {expected_size} bytes.",
            400,
        )

    session_dir = _session_dir(upload_id)
    try:
        session_dir.mkdir(parents=True, exist_ok=True)
    except OSError:
        raise ApiError("Could not prepare temporary upload storage.", 500)

    final_path = _chunk_path(session_dir, chunk_index)
    temp_path = session_dir / f".{chunk_index}.{uuid.uuid4().hex}.tmp"
    received_size = 0
    try:
        with open(temp_path, "wb") as handle:
            while True:
                piece = request.stream.read(READ_SIZE)
                if not piece:
                    break
                received_size += len(piece)
                if received_size > expected_size:
                    raise ApiError(
                        f"Chunk {chunk_index} exceeds the expected {expected_size} bytes.",
                        400,
                    )
                handle.write(piece)
            handle.flush()
            os.fsync(handle.fileno())
        if received_size != expected_size:
            raise ApiError(
                f"Chunk {chunk_index} must be exactly {expected_size} bytes.",
                400,
            )
        os.replace(temp_path, final_path)
    except ApiError:
        raise
    except OSError:
        raise ApiError("The chunk could not be stored. Please retry.", 500)
    finally:
        if temp_path.exists():
            temp_path.unlink(missing_ok=True)

    received, received_bytes = _scan_received(
        session_dir, int(row["total_chunks"])
    )
    timestamp = _to_iso(now)
    try:
        connection.execute(
            "UPDATE upload_sessions SET received_bytes = ?, received_chunks = ?, "
            "updated_at = ? WHERE id = ? AND user_id = ?",
            (received_bytes, len(received), timestamp, row["id"], user_id),
        )
        connection.commit()
    except sqlite3.Error:
        connection.rollback()
        raise ApiError("The chunk was stored but progress could not be saved.", 500)

    return jsonify(
        upload_id=upload_id,
        chunk_index=chunk_index,
        received_chunks=received,
        received_chunk_count=len(received),
        received_bytes=received_bytes,
        total_chunks=int(row["total_chunks"]),
        total_size=int(row["total_size"]),
        status="in_progress",
    )


# ---------------------------------------------------------------------------
# GET /api/uploads/<id>/status
# ---------------------------------------------------------------------------

@bp.get("/<upload_id>/status")
@api_login_required
def upload_status(upload_id):
    user_id = g.user["id"]
    connection = database.get_db()
    row = _load_session(connection, upload_id, user_id)
    now = _utcnow()
    received, received_bytes = _scan_received(
        _session_dir(upload_id), int(row["total_chunks"])
    )
    missing = _missing_chunks(received, int(row["total_chunks"]))
    expired = _is_expired(row, now)
    status = "expired" if row["status"] == "in_progress" and expired else row["status"]

    return jsonify(
        upload_id=upload_id,
        original_filename=row["original_filename"],
        folder_id=row["folder_id"],
        total_size=int(row["total_size"]),
        chunk_size=CHUNK_SIZE,
        total_chunks=int(row["total_chunks"]),
        received_chunks=received,
        missing_chunks=missing,
        received_chunk_count=len(received),
        received_bytes=received_bytes,
        status=status,
        completed=row["status"] == "completed",
        expired=expired,
        created_at=row["created_at"],
        expires_at=_expires_iso(row),
    )


# ---------------------------------------------------------------------------
# POST /api/uploads/<id>/complete
# ---------------------------------------------------------------------------

@bp.post("/<upload_id>/complete")
@api_login_required
def complete_upload(upload_id):
    user_id = g.user["id"]
    connection = database.get_db()
    row = _load_session(connection, upload_id, user_id)
    now = _utcnow()
    _assert_active(row, now)

    total_size = int(row["total_size"])
    total_chunks = int(row["total_chunks"])
    if total_size <= 0:
        raise ApiError("Upload session has an invalid size.", 409)
    if total_size > int(current_app.config["MAX_CONTENT_LENGTH"]):
        raise ApiError("The file exceeds the maximum allowed upload size.", 413)
    if not _allowed_filename(row["original_filename"]):
        raise ApiError("That file type is no longer allowed.", 409)
    if not _folder_belongs_to_user(connection, row["folder_id"], user_id):
        raise ApiError("Destination folder no longer exists.", 409)

    session_dir = _session_dir(upload_id)
    received, received_bytes = _scan_received(session_dir, total_chunks)
    missing = _missing_chunks(received, total_chunks)
    if missing:
        raise ApiError(
            "Upload is incomplete. Missing chunks: "
            + ", ".join(str(index) for index in missing),
            409,
        )

    invalid = []
    for index in range(total_chunks):
        expected = _expected_chunk_size(row, index)
        try:
            actual = _chunk_path(session_dir, index).stat().st_size
        except OSError:
            invalid.append(index)
            continue
        if actual != expected:
            invalid.append(index)
    if invalid:
        raise ApiError(
            "Chunk size validation failed for chunks: "
            + ", ".join(str(index) for index in invalid),
            409,
        )

    user_row = connection.execute(
        "SELECT used_bytes, quota_bytes FROM users WHERE id = ?", (user_id,)
    ).fetchone()
    if user_row["used_bytes"] + total_size > _effective_quota(user_row):
        raise ApiError("This upload would exceed your storage quota.", 409)

    assembled_path = session_dir / f".assembled-{uuid.uuid4().hex}.tmp"
    final_path = None
    created_backup = None
    expired_backups = []
    digest = hashlib.sha256()
    assembled = 0
    timestamp = _to_iso(now)

    try:
        with open(assembled_path, "wb") as output:
            for index in range(total_chunks):
                with open(_chunk_path(session_dir, index), "rb") as source:
                    while True:
                        piece = source.read(READ_SIZE)
                        if not piece:
                            break
                        assembled += len(piece)
                        if assembled > total_size:
                            raise ApiError("Assembled file exceeds the declared size.", 409)
                        digest.update(piece)
                        output.write(piece)
        if assembled != total_size:
            raise ApiError(
                f"Assembled size {assembled} does not match declared size {total_size}.",
                409,
            )

        stored_name = uuid.uuid4().hex
        try:
            final_path = _user_storage_dir(user_id) / stored_name
            os.replace(assembled_path, final_path)
        except OSError:
            raise ApiError("The completed file could not be stored.", 500)
    except OSError:
        raise ApiError("The upload could not be assembled. Please retry.", 500)
    finally:
        if assembled_path.exists():
            assembled_path.unlink(missing_ok=True)

    checksum = digest.hexdigest()
    mime_type = (
        mimetypes.guess_type(row["original_filename"])[0]
        or "application/octet-stream"
    )

    try:
        cursor = connection.execute(
            "UPDATE upload_sessions SET status = 'completed', received_bytes = ?, "
            "received_chunks = ?, updated_at = ? "
            "WHERE id = ? AND user_id = ? AND status = 'in_progress'",
            (total_size, total_chunks, timestamp, row["id"], user_id),
        )
        if cursor.rowcount != 1:
            raise ApiError("Upload session is no longer active.", 409)

        current_user_row = connection.execute(
            "SELECT used_bytes, quota_bytes FROM users WHERE id = ?", (user_id,)
        ).fetchone()
        if (
            current_user_row["used_bytes"] + total_size
            > _effective_quota(current_user_row)
        ):
            raise ApiError("This upload would exceed your storage quota.", 409)

        file_cursor = connection.execute(
            "INSERT INTO files (user_id, folder_id, original_filename, "
            "stored_filename, size_bytes, mime_type, checksum_sha256) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                user_id,
                row["folder_id"],
                row["original_filename"],
                stored_name,
                total_size,
                mime_type,
                checksum,
            ),
        )
        file_id = file_cursor.lastrowid
        created_backup, expired_backups = create_initial_backup(
            connection, user_id, file_id, final_path
        )
        connection.execute(
            "UPDATE upload_sessions SET file_id = ? WHERE id = ?",
            (file_id, row["id"]),
        )
        _recalculate_used_bytes(connection, user_id)
        connection.commit()
    except ApiError:
        connection.rollback()
        if created_backup is not None:
            created_backup.unlink(missing_ok=True)
        if final_path is not None:
            final_path.unlink(missing_ok=True)
        raise
    except (sqlite3.Error, OSError):
        connection.rollback()
        if created_backup is not None:
            created_backup.unlink(missing_ok=True)
        if final_path is not None:
            final_path.unlink(missing_ok=True)
        raise ApiError("The upload and its initial backup could not be finalized. Please retry.", 500)

    remove_expired_backups(expired_backups)
    shutil.rmtree(session_dir, ignore_errors=True)

    return jsonify(
        upload_id=upload_id,
        file_id=file_id,
        original_filename=row["original_filename"],
        size_bytes=total_size,
        checksum_sha256=checksum,
        mime_type=mime_type,
        folder_id=row["folder_id"],
        status="completed",
        download_url=url_for("files.download_file", file_id=file_id),
        detail_url=url_for("files.file_detail", file_id=file_id),
    ), 201


# ---------------------------------------------------------------------------
# Expired-session cleanup (CLI)
# ---------------------------------------------------------------------------

def cleanup_expired_uploads(connection, temporary_root, now=None):
    """Remove expired session directories and abort stale in-progress rows.

    Only sessions older than the 24-hour TTL are touched. Completed permanent
    files live under ``storage/`` and are never modified. The operation is
    idempotent and is safe to run repeatedly.
    """
    now = now or _utcnow()
    cutoff = _to_iso(now - SESSION_TTL)
    rows = connection.execute(
        "SELECT id, upload_id, status FROM upload_sessions "
        "WHERE status IN ('in_progress', 'completed', 'aborted') "
        "AND created_at <= ?",
        (cutoff,),
    ).fetchall()

    stats = {
        "sessions_scanned": len(rows),
        "directories_removed": 0,
        "sessions_aborted": 0,
        "errors": 0,
    }

    for row in rows:
        path = _cleanup_session_dir(temporary_root, row["upload_id"])
        if path is not None and path.is_dir():
            try:
                shutil.rmtree(path)
                stats["directories_removed"] += 1
            except OSError:
                stats["errors"] += 1

        if row["status"] == "in_progress":
            try:
                connection.execute(
                    "UPDATE upload_sessions SET status = 'aborted', "
                    "received_bytes = 0, received_chunks = 0, updated_at = ? "
                    "WHERE id = ? AND status = 'in_progress'",
                    (_to_iso(now), row["id"]),
                )
                stats["sessions_aborted"] += 1
            except sqlite3.Error:
                stats["errors"] += 1

    connection.commit()
    return stats
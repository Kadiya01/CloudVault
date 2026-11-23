"""Secure shared links and public downloads (Stage 6, Part 2).

Share ownership is always derived from the authenticated session. Public
endpoints expose only limited file metadata and revalidate the token, expiry,
revocation and active-file state on every request.
"""

import secrets
from datetime import datetime, timezone
from functools import wraps
from pathlib import Path

from flask import (
    Blueprint, abort, current_app, g, jsonify, render_template, request, send_file
)

from . import database

bp = Blueprint("shares", __name__)


def api_login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if g.get("user") is None:
            return jsonify(error="Authentication required."), 401
        return view(*args, **kwargs)
    return wrapped


def _iso_utc(value):
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    except ValueError:
        return None


def _db_timestamp(moment):
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def _public_share(connection, token):
    return connection.execute(
        "SELECT s.id AS share_id, s.file_id, s.user_id AS share_owner_id, "
        "s.token, s.expires_at, s.revoked, s.created_at, "
        "f.original_filename, f.size_bytes, f.mime_type, f.stored_filename, "
        "f.status AS file_status, f.user_id AS file_owner_id "
        "FROM shares s JOIN files f ON f.id = s.file_id WHERE s.token = ?",
        (token,),
    ).fetchone()


def _share_state(row):
    if row is None:
        return "not_found"
    if row["revoked"]:
        return "revoked"
    expiry = _iso_utc(row["expires_at"])
    if expiry is None or datetime.now(timezone.utc) >= expiry:
        return "expired"
    if row["file_status"] != "active" or row["share_owner_id"] != row["file_owner_id"]:
        return "not_found"
    return None


def _public_error(state):
    messages = {
        "expired": "This shared link has expired.",
        "revoked": "This shared link has been revoked.",
        "not_found": "The requested shared file was not found.",
    }
    status = 410 if state in {"expired", "revoked"} else 404
    return jsonify(error=messages[state], state=state), status


def _stored_path(row):
    stored_name = row["stored_filename"] or ""
    if len(stored_name) != 32 or any(ch not in "0123456789abcdef" for ch in stored_name):
        raise ValueError("Invalid stored filename.")
    root = Path(current_app.config["STORAGE_DIR"]).resolve()
    owner_dir = (root / str(row["file_owner_id"])).resolve()
    candidate = owner_dir / stored_name
    if candidate.is_symlink():
        raise ValueError("Symbolic links are not valid stored files.")
    path = candidate.resolve()
    if owner_dir.parent != root or path.parent != owner_dir:
        raise ValueError("Stored file path escaped the storage directory.")
    return path


@bp.get("/shares")
@api_login_required
def shared_links_page():
    return render_template("shares.html")


@bp.get("/api/files/active")
@api_login_required
def list_active_files_for_sharing():
    connection = database.get_db()
    rows = connection.execute(
        "SELECT id, original_filename FROM files "
        "WHERE user_id = ? AND status = 'active' "
        "ORDER BY lower(original_filename), id",
        (g.user["id"],),
    ).fetchall()
    return jsonify(files=[
        {"id": row["id"], "original_filename": row["original_filename"]}
        for row in rows
    ])


@bp.get("/api/shares")
@api_login_required
def list_shares():
    connection = database.get_db()
    rows = connection.execute(
        "SELECT s.id, s.file_id, s.token, s.expires_at, s.revoked, s.created_at, "
        "f.original_filename, f.status AS file_status "
        "FROM shares s JOIN files f ON f.id = s.file_id "
        "WHERE s.user_id = ? ORDER BY s.created_at DESC, s.id DESC",
        (g.user["id"],),
    ).fetchall()
    now = datetime.now(timezone.utc)
    result = []
    for row in rows:
        expiry = _iso_utc(row["expires_at"])
        result.append({
            "id": row["id"],
            "file_id": row["file_id"],
            "token": row["token"],
            "original_filename": row["original_filename"],
            "expires_at": row["expires_at"],
            "created_at": row["created_at"],
            "revoked": bool(row["revoked"]) or row["file_status"] != "active",
            "is_expired": expiry is None or now >= expiry,
        })
    return jsonify(shares=result)


@bp.post("/api/shares")
@api_login_required
def create_share():
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return jsonify(error="A JSON request body is required."), 400

    file_id = payload.get("file_id")
    if isinstance(file_id, bool) or not isinstance(file_id, int) or file_id <= 0:
        return jsonify(error="Select a valid file to share."), 400

    expiry = _iso_utc(payload.get("expires_at"))
    now = datetime.now(timezone.utc)
    if expiry is None or expiry <= now:
        return jsonify(error="Expiration must be a valid future date and time."), 400

    connection = database.get_db()
    user_id = g.user["id"]
    file_row = connection.execute(
        "SELECT id FROM files WHERE id = ? AND user_id = ? AND status = 'active'",
        (file_id, user_id),
    ).fetchone()
    if file_row is None:
        return jsonify(error="Active file not found."), 404

    token = secrets.token_urlsafe(32)
    created_at = _db_timestamp(now)
    expires_at = _db_timestamp(expiry)
    try:
        cursor = connection.execute(
            "INSERT INTO shares (file_id, user_id, token, expires_at) "
            "VALUES (?, ?, ?, ?)",
            (file_id, user_id, token, expires_at),
        )
        share_id = cursor.lastrowid
        connection.execute(
            "INSERT INTO activity_logs "
            "(user_id, action, entity_type, entity_id, details, ip_address) "
            "VALUES (?, 'share_created', 'share', ?, ?, ?)",
            (user_id, share_id, '{"file_id":' + str(file_id) + '}', request.remote_addr),
        )
        connection.commit()
    except Exception:
        connection.rollback()
        raise

    return jsonify(
        success=True,
        message="Share link created successfully.",
        share={
            "id": share_id,
            "file_id": file_id,
            "token": token,
            "expires_at": expires_at,
            "created_at": created_at,
            "revoked": False,
            "is_expired": False,
        },
    ), 201


@bp.post("/api/shares/<int:share_id>/revoke")
@api_login_required
def revoke_share(share_id):
    connection = database.get_db()
    user_id = g.user["id"]
    row = connection.execute(
        "SELECT id, revoked FROM shares WHERE id = ? AND user_id = ?",
        (share_id, user_id),
    ).fetchone()
    if row is None:
        return jsonify(error="Share link not found."), 404
    if row["revoked"]:
        return jsonify(success=True, message="Share link is already revoked.")
    try:
        connection.execute(
            "UPDATE shares SET revoked = 1 WHERE id = ? AND user_id = ?",
            (share_id, user_id),
        )
        connection.execute(
            "INSERT INTO activity_logs "
            "(user_id, action, entity_type, entity_id, details, ip_address) "
            "VALUES (?, 'share_revoked', 'share', ?, '{}', ?)",
            (user_id, share_id, request.remote_addr),
        )
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    return jsonify(success=True, message="Share link revoked successfully.")


@bp.get("/api/s/<token>")
def public_share_metadata(token):
    row = _public_share(database.get_db(), token)
    state = _share_state(row)
    if state:
        return _public_error(state)
    return jsonify(
        file={
            "original_filename": row["original_filename"],
            "size_bytes": row["size_bytes"],
            "mime_type": row["mime_type"],
        },
        share={"expires_at": row["expires_at"]},
    )


@bp.get("/s/<token>")
def public_share_page(token):
    row = _public_share(database.get_db(), token)
    state = _share_state(row)
    if state:
        return render_template(
            "share_download.html",
            error_state=state,
            expires_at=row["expires_at"] if row is not None else None,
            file=None,
            token=token,
        ), (410 if state in {"expired", "revoked"} else 404)
    return render_template(
        "share_download.html",
        error_state=None,
        expires_at=row["expires_at"],
        file=row,
        token=token,
    )


@bp.get("/s/<token>/download")
def download_shared_file(token):
    connection = database.get_db()
    row = _public_share(connection, token)
    state = _share_state(row)
    if state:
        abort(410 if state in {"expired", "revoked"} else 404)
    try:
        path = _stored_path(row)
    except ValueError:
        current_app.logger.exception("Unsafe public share storage path.")
        abort(404)
    if not path.is_file():
        abort(404)
    response = send_file(
        path,
        as_attachment=True,
        download_name=row["original_filename"],
        mimetype=row["mime_type"] or "application/octet-stream",
        conditional=True,
        max_age=0,
    )
    response.headers["Cache-Control"] = "private, no-store"
    response.headers["X-Content-Type-Options"] = "nosniff"
    return response

"""File, folder and tag management (Stage 3).

All operations require an authenticated user and enforce ownership on the
server. Physical files live under ``storage/<user_id>/`` and are never exposed
as public static assets; downloads always pass an ownership check first.

Resumable uploads (Stage 4), versioned backups (Stage 5) and permanent deletion
/ restore / sharing (Stage 6) are intentionally not implemented here.
"""

import hashlib
import os
import re
import sqlite3
import uuid
from pathlib import Path

from flask import (
    Blueprint,
    abort,
    current_app,
    flash,
    g,
    redirect,
    render_template,
    request,
    send_file,
    url_for,
)
from werkzeug.utils import secure_filename

from . import database
from .auth import login_required
from .versioning import create_initial_backup, remove_expired_backups

bp = Blueprint("files", __name__)

STORED_NAME_RE = re.compile(r"^[0-9a-f]{32}$")
CHUNK_SIZE = 64 * 1024  # 64 KB, as required for SHA-256 hashing.


class UploadTooLarge(Exception):
    """Raised while streaming when an upload exceeds the configured limit."""


# ---------------------------------------------------------------------------
# Ownership-aware lookups. Missing and foreign records both return 404 so the
# existence of another user's data is not revealed.
# ---------------------------------------------------------------------------

def _get_folder_or_404(connection, folder_id, user_id):
    row = connection.execute(
        "SELECT * FROM folders WHERE id = ? AND user_id = ?",
        (folder_id, user_id),
    ).fetchone()
    if row is None:
        abort(404)
    return row


def _get_file_or_404(connection, file_id, user_id):
    row = connection.execute(
        "SELECT * FROM files WHERE id = ? AND user_id = ?",
        (file_id, user_id),
    ).fetchone()
    if row is None:
        abort(404)
    return row


def _get_tag_or_404(connection, tag_id, user_id):
    row = connection.execute(
        "SELECT * FROM tags WHERE id = ? AND user_id = ?",
        (tag_id, user_id),
    ).fetchone()
    if row is None:
        abort(404)
    return row


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------

def _parse_id(value):
    value = (value or "").strip()
    return int(value) if value.isdigit() and int(value) > 0 else None


def _redirect_folder(folder_id):
    if folder_id:
        return redirect(url_for("files.index", folder_id=folder_id))
    return redirect(url_for("files.index"))


def _redirect_after_action():
    """Return to a file detail page or folder after a tag action."""
    file_id = _parse_id(request.form.get("return_file_id", ""))
    if file_id:
        return redirect(url_for("files.file_detail", file_id=file_id))
    return _redirect_folder(_parse_id(request.form.get("return_folder_id", "")))


def _escape_like(term):
    return (
        "%"
        + term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        + "%"
    )


def _allowed_filename(name):
    if not name or name in {".", ".."} or len(name) > 255:
        return False
    if "." not in name:
        return False
    extension = name.rsplit(".", 1)[1].lower()
    return extension in current_app.config.get("ALLOWED_EXTENSIONS", frozenset())


def _user_storage_dir(user_id):
    directory = Path(current_app.config["STORAGE_DIR"]) / str(user_id)
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def _temporary_dir():
    directory = Path(current_app.config["TEMPORARY_UPLOADS_DIR"])
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def _stored_file_path(user_id, stored_filename):
    """Resolve a stored file safely, rejecting traversal and odd names."""
    if not STORED_NAME_RE.fullmatch(stored_filename or ""):
        abort(404)
    base = _user_storage_dir(user_id).resolve()
    path = (base / stored_filename).resolve()
    if path.parent != base:
        abort(404)
    return path


def _effective_quota(user_row):
    """Quota in bytes, falling back to the configured default for 0 values."""
    quota = user_row["quota_bytes"]
    if quota:
        return int(quota)
    return int(current_app.config.get("DEFAULT_USER_QUOTA_BYTES", 0))


def _recalculate_used_bytes(connection, user_id):
    """Recompute users.used_bytes from files (includes soft-deleted files)."""
    connection.execute(
        "UPDATE users SET used_bytes = ("
        "  SELECT COALESCE(SUM(size_bytes), 0) FROM files WHERE user_id = ?"
        ") WHERE id = ?",
        (user_id, user_id),
    )


def _tags_for_files(connection, user_id, file_ids):
    """Return {file_id: [ {id, name}, ... ]} for the supplied files."""
    if not file_ids:
        return {}
    placeholders = ",".join("?" for _ in file_ids)
    rows = connection.execute(
        f"SELECT ft.file_id AS file_id, t.id AS tag_id, t.name AS tag_name "
        f"FROM file_tags ft JOIN tags t ON t.id = ft.tag_id "
        f"WHERE ft.user_id = ? AND ft.file_id IN ({placeholders}) "
        f"ORDER BY lower(t.name)",
        [user_id, *file_ids],
    ).fetchall()
    result = {}
    for row in rows:
        result.setdefault(row["file_id"], []).append(
            {"id": row["tag_id"], "name": row["tag_name"]}
        )
    return result


def _breadcrumbs(connection, user_id, folder):
    """Return breadcrumb folders from the root down to ``folder``."""
    crumbs = []
    seen = set()
    current = folder
    while current is not None and current["id"] not in seen:
        seen.add(current["id"])
        crumbs.append(current)
        if current["parent_id"] is None:
            break
        current = connection.execute(
            "SELECT * FROM folders WHERE id = ? AND user_id = ?",
            (current["parent_id"], user_id),
        ).fetchone()
    crumbs.reverse()
    return crumbs


def _folder_within(connection, user_id, folder_id, candidate_id):
    """True when candidate_id is folder_id or one of its descendants."""
    current = candidate_id
    while current is not None:
        if current == folder_id:
            return True
        row = connection.execute(
            "SELECT parent_id FROM folders WHERE id = ? AND user_id = ?",
            (current, user_id),
        ).fetchone()
        if row is None:
            return True
        current = row["parent_id"]
    return False


# ---------------------------------------------------------------------------
# My Files / folder listing
# ---------------------------------------------------------------------------

@bp.route("/files")
@bp.route("/folders/<int:folder_id>")
@login_required
def index(folder_id=None):
    user_id = g.user["id"]
    connection = database.get_db()

    current_folder = (
        _get_folder_or_404(connection, folder_id, user_id) if folder_id else None
    )
    breadcrumbs = (
        _breadcrumbs(connection, user_id, current_folder) if current_folder else []
    )

    if folder_id:
        folder_clause = "parent_id = ?"
        file_clause = "folder_id = ?"
        base_params = [user_id, folder_id]
    else:
        folder_clause = "parent_id IS NULL"
        file_clause = "folder_id IS NULL"
        base_params = [user_id]

    folders = connection.execute(
        f"SELECT * FROM folders WHERE user_id = ? AND {folder_clause} "
        f"ORDER BY lower(name)",
        base_params,
    ).fetchall()

    search = request.args.get("search", "").strip()
    active_tag = _parse_id(request.args.get("tag", ""))

    file_sql = (
        f"SELECT * FROM files WHERE user_id = ? AND status = 'active' "
        f"AND {file_clause}"
    )
    file_params = list(base_params)
    if search:
        file_sql += " AND original_filename LIKE ? ESCAPE '\\'"
        file_params.append(_escape_like(search))
    if active_tag:
        file_sql += (
            " AND id IN (SELECT file_id FROM file_tags "
            "WHERE user_id = ? AND tag_id = ?)"
        )
        file_params.extend([user_id, active_tag])
    file_sql += " ORDER BY lower(original_filename)"
    files = connection.execute(file_sql, file_params).fetchall()

    tags = connection.execute(
        "SELECT * FROM tags WHERE user_id = ? ORDER BY lower(name)", (user_id,)
    ).fetchall()
    file_tags = _tags_for_files(
        connection, user_id, [row["id"] for row in files]
    )
    all_folders = connection.execute(
        "SELECT * FROM folders WHERE user_id = ? ORDER BY lower(name)", (user_id,)
    ).fetchall()

    used_bytes = connection.execute(
        "SELECT used_bytes FROM users WHERE id = ?", (user_id,)
    ).fetchone()["used_bytes"]
    quota_bytes = _effective_quota(g.user)
    quota_percent = (
        min(100, round(used_bytes * 100 / quota_bytes, 1))
        if quota_bytes > 0
        else 100
    )

    return render_template(
        "files.html",
        current_folder=current_folder,
        breadcrumbs=breadcrumbs,
        folders=folders,
        files=files,
        tags=tags,
        file_tags=file_tags,
        all_folders=all_folders,
        search=search,
        active_tag=active_tag,
        used_bytes=used_bytes,
        quota_bytes=quota_bytes,
        quota_percent=quota_percent,
        allowed_extensions=sorted(
            current_app.config.get("ALLOWED_EXTENSIONS", frozenset())
        ),
    )


# ---------------------------------------------------------------------------
# Folder operations
# ---------------------------------------------------------------------------

@bp.post("/folders")
@login_required
def create_folder():
    user_id = g.user["id"]
    connection = database.get_db()
    name = request.form.get("name", "").strip()
    parent_id = _parse_id(request.form.get("parent_id", ""))
    if parent_id:
        _get_folder_or_404(connection, parent_id, user_id)

    if not name or len(name) > 255:
        flash("Folder name must be between 1 and 255 characters.", "error")
        return _redirect_folder(parent_id)

    try:
        connection.execute(
            "INSERT INTO folders (user_id, parent_id, name) VALUES (?, ?, ?)",
            (user_id, parent_id, name),
        )
        connection.commit()
    except sqlite3.IntegrityError:
        connection.rollback()
        flash("A folder with that name already exists in this location.", "error")
    else:
        flash("Folder created.", "success")
    return _redirect_folder(parent_id)


@bp.post("/folders/<int:folder_id>/rename")
@login_required
def rename_folder(folder_id):
    user_id = g.user["id"]
    connection = database.get_db()
    folder = _get_folder_or_404(connection, folder_id, user_id)
    name = request.form.get("name", "").strip()

    if not name or len(name) > 255:
        flash("Folder name must be between 1 and 255 characters.", "error")
        return _redirect_folder(folder["parent_id"])

    try:
        connection.execute(
            "UPDATE folders SET name = ?, updated_at = "
            "strftime('%Y-%m-%dT%H:%M:%fZ', 'now') "
            "WHERE id = ? AND user_id = ?",
            (name, folder_id, user_id),
        )
        connection.commit()
    except sqlite3.IntegrityError:
        connection.rollback()
        flash("A folder with that name already exists in this location.", "error")
    else:
        flash("Folder renamed.", "success")
    return _redirect_folder(folder["parent_id"])


@bp.post("/folders/<int:folder_id>/move")
@login_required
def move_folder(folder_id):
    user_id = g.user["id"]
    connection = database.get_db()
    folder = _get_folder_or_404(connection, folder_id, user_id)
    target_id = _parse_id(request.form.get("parent_id", ""))

    if target_id:
        _get_folder_or_404(connection, target_id, user_id)
        if _folder_within(connection, user_id, folder_id, target_id):
            flash("A folder cannot be moved inside itself or its own subfolder.", "error")
            return _redirect_folder(folder["parent_id"])

    try:
        connection.execute(
            "UPDATE folders SET parent_id = ?, updated_at = "
            "strftime('%Y-%m-%dT%H:%M:%fZ', 'now') "
            "WHERE id = ? AND user_id = ?",
            (target_id, folder_id, user_id),
        )
        connection.commit()
    except sqlite3.IntegrityError:
        connection.rollback()
        flash("A folder with that name already exists in the destination.", "error")
        return _redirect_folder(folder["parent_id"])
    else:
        flash("Folder moved.", "success")
    return _redirect_folder(target_id)


# ---------------------------------------------------------------------------
# File upload and download
# ---------------------------------------------------------------------------

@bp.post("/files/upload")
@login_required
def upload_file():
    user_id = g.user["id"]
    connection = database.get_db()
    folder_id = _parse_id(request.form.get("folder_id", ""))
    if folder_id:
        _get_folder_or_404(connection, folder_id, user_id)

    upload = request.files.get("file")
    if upload is None or not upload.filename:
        flash("Choose a file to upload.", "error")
        return _redirect_folder(folder_id)

    safe_name = secure_filename(upload.filename)
    if not _allowed_filename(safe_name):
        flash(
            "That file type is not allowed. Allowed extensions: "
            + ", ".join(sorted(current_app.config.get("ALLOWED_EXTENSIONS", ())))
            + ".",
            "error",
        )
        return _redirect_folder(folder_id)

    max_bytes = int(current_app.config["MAX_CONTENT_LENGTH"])
    stored_name = uuid.uuid4().hex
    temp_path = _temporary_dir() / f"{stored_name}.part"
    digest = hashlib.sha256()
    size = 0

    try:
        try:
            with open(temp_path, "wb") as handle:
                while True:
                    chunk = upload.stream.read(CHUNK_SIZE)
                    if not chunk:
                        break
                    size += len(chunk)
                    if size > max_bytes:
                        raise UploadTooLarge
                    digest.update(chunk)
                    handle.write(chunk)
        except UploadTooLarge:
            flash("The file exceeds the maximum allowed upload size.", "error")
            return _redirect_folder(folder_id)
        except OSError:
            flash("The file could not be saved. Please try again.", "error")
            return _redirect_folder(folder_id)

        if size == 0:
            flash("Empty files cannot be uploaded.", "error")
            return _redirect_folder(folder_id)

        user_row = connection.execute(
            "SELECT used_bytes, quota_bytes FROM users WHERE id = ?", (user_id,)
        ).fetchone()
        if user_row["used_bytes"] + size > _effective_quota(user_row):
            flash(
                "This upload would exceed your storage quota. Files in the "
                "recycle bin still count until they are permanently removed.",
                "error",
            )
            return _redirect_folder(folder_id)

        final_path = _user_storage_dir(user_id) / stored_name
        created_backup = None
        expired_backups = []
        try:
            os.replace(temp_path, final_path)
        except OSError:
            flash("The file could not be saved. Please try again.", "error")
            return _redirect_folder(folder_id)

        try:
            cursor = connection.execute(
                "INSERT INTO files (user_id, folder_id, original_filename, "
                "stored_filename, size_bytes, mime_type, checksum_sha256) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    user_id,
                    folder_id,
                    safe_name,
                    stored_name,
                    size,
                    upload.mimetype or "application/octet-stream",
                    digest.hexdigest(),
                ),
            )
            file_id = cursor.lastrowid
            created_backup, expired_backups = create_initial_backup(
                connection, user_id, file_id, final_path
            )
            _recalculate_used_bytes(connection, user_id)
            connection.commit()
        except (sqlite3.Error, OSError):
            connection.rollback()
            if created_backup is not None:
                created_backup.unlink(missing_ok=True)
            final_path.unlink(missing_ok=True)
            flash("The file and its initial backup could not be recorded. Please try again.", "error")
        else:
            remove_expired_backups(expired_backups)
            flash(f"'{safe_name}' uploaded with its initial backup.", "success")
    finally:
        if temp_path.exists():
            temp_path.unlink(missing_ok=True)

    return _redirect_folder(folder_id)


@bp.get("/files/<int:file_id>")
@login_required
def file_detail(file_id):
    user_id = g.user["id"]
    connection = database.get_db()
    file_row = _get_file_or_404(connection, file_id, user_id)
    file_tags = _tags_for_files(connection, user_id, [file_row["id"]]).get(
        file_row["id"], []
    )
    all_tags = connection.execute(
        "SELECT * FROM tags WHERE user_id = ? ORDER BY lower(name)", (user_id,)
    ).fetchall()
    folders = connection.execute(
        "SELECT * FROM folders WHERE user_id = ? ORDER BY lower(name)", (user_id,)
    ).fetchall()
    return render_template(
        "file_detail.html",
        file=file_row,
        file_tags=file_tags,
        all_tags=all_tags,
        folders=folders,
    )


@bp.get("/files/<int:file_id>/download")
@login_required
def download_file(file_id):
    user_id = g.user["id"]
    connection = database.get_db()
    file_row = _get_file_or_404(connection, file_id, user_id)
    if file_row["status"] != "active":
        abort(404)

    path = _stored_file_path(user_id, file_row["stored_filename"])
    if not path.is_file():
        flash("The stored file data is missing on the server.", "error")
        return redirect(url_for("files.file_detail", file_id=file_id))

    return send_file(
        path,
        as_attachment=True,
        download_name=file_row["original_filename"],
        mimetype=file_row["mime_type"] or "application/octet-stream",
    )


@bp.post("/files/<int:file_id>/rename")
@login_required
def rename_file(file_id):
    user_id = g.user["id"]
    connection = database.get_db()
    file_row = _get_file_or_404(connection, file_id, user_id)
    safe_name = secure_filename(request.form.get("name", ""))

    if not _allowed_filename(safe_name):
        flash("Enter a valid filename with an allowed extension.", "error")
        return _redirect_folder(file_row["folder_id"])

    connection.execute(
        "UPDATE files SET original_filename = ?, updated_at = "
        "strftime('%Y-%m-%dT%H:%M:%fZ', 'now') "
        "WHERE id = ? AND user_id = ?",
        (safe_name, file_id, user_id),
    )
    connection.commit()
    flash("File renamed.", "success")
    return _redirect_folder(file_row["folder_id"])


@bp.post("/files/<int:file_id>/move")
@login_required
def move_file(file_id):
    user_id = g.user["id"]
    connection = database.get_db()
    file_row = _get_file_or_404(connection, file_id, user_id)
    target_id = _parse_id(request.form.get("folder_id", ""))
    if target_id:
        _get_folder_or_404(connection, target_id, user_id)

    connection.execute(
        "UPDATE files SET folder_id = ?, updated_at = "
        "strftime('%Y-%m-%dT%H:%M:%fZ', 'now') "
        "WHERE id = ? AND user_id = ?",
        (target_id, file_id, user_id),
    )
    connection.commit()
    flash("File moved.", "success")
    return _redirect_folder(target_id)


@bp.post("/files/<int:file_id>/delete")
@login_required
def delete_file(file_id):
    user_id = g.user["id"]
    connection = database.get_db()
    file_row = _get_file_or_404(connection, file_id, user_id)

    connection.execute(
        "UPDATE files SET status = 'deleted', deleted_at = "
        "strftime('%Y-%m-%dT%H:%M:%fZ', 'now'), updated_at = "
        "strftime('%Y-%m-%dT%H:%M:%fZ', 'now') "
        "WHERE id = ? AND user_id = ? AND status = 'active'",
        (file_id, user_id),
    )
    _recalculate_used_bytes(connection, user_id)
    connection.commit()
    flash("File moved to the recycle bin.", "success")
    return _redirect_folder(file_row["folder_id"])


# ---------------------------------------------------------------------------
# Tags
# ---------------------------------------------------------------------------

@bp.post("/tags")
@login_required
def create_tag():
    user_id = g.user["id"]
    connection = database.get_db()
    name = request.form.get("name", "").strip()

    if not name or len(name) > 50:
        flash("Tag name must be between 1 and 50 characters.", "error")
        return _redirect_after_action()

    try:
        connection.execute(
            "INSERT INTO tags (user_id, name) VALUES (?, ?)", (user_id, name)
        )
        connection.commit()
    except sqlite3.IntegrityError:
        connection.rollback()
        flash("You already have a tag with that name.", "error")
    else:
        flash("Tag created.", "success")
    return _redirect_after_action()


@bp.post("/files/<int:file_id>/tags")
@login_required
def add_tag_to_file(file_id):
    user_id = g.user["id"]
    connection = database.get_db()
    _get_file_or_404(connection, file_id, user_id)
    tag_id = _parse_id(request.form.get("tag_id", ""))
    if not tag_id:
        flash("Choose a tag to add.", "error")
        return _redirect_after_action()
    _get_tag_or_404(connection, tag_id, user_id)

    try:
        connection.execute(
            "INSERT INTO file_tags (file_id, tag_id, user_id) VALUES (?, ?, ?)",
            (file_id, tag_id, user_id),
        )
        connection.commit()
    except sqlite3.IntegrityError:
        connection.rollback()
        flash("That tag is already assigned to this file.", "error")
    else:
        flash("Tag added.", "success")
    return _redirect_after_action()


@bp.post("/files/<int:file_id>/tags/<int:tag_id>/delete")
@login_required
def remove_tag_from_file(file_id, tag_id):
    user_id = g.user["id"]
    connection = database.get_db()
    _get_file_or_404(connection, file_id, user_id)
    connection.execute(
        "DELETE FROM file_tags WHERE file_id = ? AND tag_id = ? AND user_id = ?",
        (file_id, tag_id, user_id),
    )
    connection.commit()
    flash("Tag removed.", "success")
    return _redirect_after_action()

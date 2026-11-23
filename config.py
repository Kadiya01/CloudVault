"""Application configuration for CloudVault.

Configuration is intentionally environment-driven so that secrets are never
hardcoded. Authentication (Stage 2) requires a real secret key.
"""

import os
from datetime import timedelta
from pathlib import Path


class Config:
    """Base configuration shared by all environments."""

    BASE_DIR = Path(__file__).resolve().parent
    INSTANCE_DIR = BASE_DIR / "instance"
    DATABASE = str(INSTANCE_DIR / "cloudvault.db")
    SCHEMA_PATH = BASE_DIR / "schema.sql"

    STORAGE_DIR = BASE_DIR / "storage"
    BACKUP_DIR = BASE_DIR / "backup"
    TEMPORARY_UPLOADS_DIR = BASE_DIR / "temporary_uploads"

    # Read from the environment; never hardcode a production secret. The
    # application factory fails clearly if this is missing at runtime.
    SECRET_KEY = os.environ.get("CLOUDVAULT_SECRET_KEY")

    # Session cookie hardening. Secure cookies are enabled explicitly for
    # HTTPS/production deployments via CLOUDVAULT_COOKIE_SECURE=1.
    SESSION_COOKIE_NAME = "cloudvault_session"
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"
    SESSION_COOKIE_SECURE = os.environ.get(
        "CLOUDVAULT_COOKIE_SECURE", ""
    ).strip().lower() in {"1", "true", "yes", "on"}
    PERMANENT_SESSION_LIFETIME = timedelta(hours=8)

    # Upload limits (Stage 3). MAX_CONTENT_LENGTH is enforced by Flask for the
    # whole request; the allowed-extension list is checked again on the server.
    MAX_CONTENT_LENGTH = int(
        os.environ.get("CLOUDVAULT_MAX_UPLOAD_BYTES", 1024 * 1024 * 1024)
    )
    ALLOWED_EXTENSIONS = frozenset(
        extension.strip().lower()
        for extension in os.environ.get(
            "CLOUDVAULT_ALLOWED_EXTENSIONS",
            "txt,csv,json,md,pdf,doc,docx,xls,xlsx,ppt,pptx,odt,ods,"
            "png,jpg,jpeg,gif,webp,svg,zip,mp3,mp4,webm",
        ).split(",")
        if extension.strip()
    )

    # Default per-user storage quota (100 MiB) applied to new accounts.
    DEFAULT_USER_QUOTA_BYTES = int(
        os.environ.get("CLOUDVAULT_DEFAULT_QUOTA_BYTES", 100 * 1024 * 1024)
    )

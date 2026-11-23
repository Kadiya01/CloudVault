# CloudVault

CloudVault is a cloud-based file storage and backup system built with Python,
Flask, SQLite, HTML/Jinja2, plain CSS and minimal vanilla JavaScript.

This repository currently contains **Stages 1, 2, 3 and 4**:

- Stage 1: Project setup, database schema and storage directories.
- Stage 1.1: Cross-user database integrity and transaction-safe initialisation.
- Stage 2: Registration, login/logout, password hashing, session security and
  role-based access control.
- Stage 3: File and folder management, upload/download, quota enforcement,
  tags, filename/tag search and soft deletion to the recycle bin.
- Stage 4: Resumable 2 MB chunked uploads with progress, resume, retry and
  expired-session cleanup.

Versioned backups, recycle-bin restore and file sharing are **not** implemented
yet.

## Requirements

- Python 3.14 (developed against Python 3.14.10)
- The project virtual environment at `venv/`

## Setup

```powershell
# Activate the existing environment (Windows PowerShell)
.\venv\Scripts\Activate.ps1

# Install the declared dependencies
pip install -r requirements.txt

# Provide a secret key (required; there is no insecure fallback)
$env:CLOUDVAULT_SECRET_KEY = "replace-with-a-long-random-value"

# Initialise the database schema (creates instance/cloudvault.db)
flask --app app init-db

# Run the development server
python app.py
```

On Linux/macOS, activate the environment with `source venv/bin/activate` and set
the secret with `export CLOUDVAULT_SECRET_KEY="..."`.

Generate a strong secret, for example:

```powershell
python -c "import secrets; print(secrets.token_urlsafe(48))"
```

### Session cookie settings

- `SESSION_COOKIE_HTTPONLY = True`
- `SESSION_COOKIE_SAMESITE = "Lax"`
- `SESSION_COOKIE_SECURE` is **off by default** for local HTTP development.
  Enable it for HTTPS/production with `CLOUDVAULT_COOKIE_SECURE=1`.

The application refuses to start if `CLOUDVAULT_SECRET_KEY` is missing.

## Administrator provisioning

Public registration always creates a normal `user` account. Administrators are
created only through the trusted CLI command:

```powershell
flask --app app create-admin
```

The command prompts for username, email and password (password input is hidden
and confirmed). The password is hashed before storage and is never printed.
Username and email uniqueness are enforced.

## Routes

| Route | Method | Access |
|---|---|---|
| `/` | GET | Redirects to dashboard or login |
| `/register` | GET, POST | Public (logged-out users) |
| `/login` | GET, POST | Public (logged-out users) |
| `/logout` | POST | Any authenticated user |
| `/dashboard` | GET | Authenticated users |
| `/admin` | GET | Administrators only |
| `/files` | GET | Authenticated users |
| `/folders/<id>` | GET | Authenticated owner |
| `/folders` | POST | Authenticated users |
| `/folders/<id>/rename` | POST | Authenticated owner |
| `/folders/<id>/move` | POST | Authenticated owner |
| `/files/upload` | POST | Authenticated users |
| `/files/<id>` | GET | Authenticated owner |
| `/files/<id>/download` | GET | Authenticated owner |
| `/files/<id>/rename` | POST | Authenticated owner |
| `/files/<id>/move` | POST | Authenticated owner |
| `/files/<id>/delete` | POST | Authenticated owner (soft delete) |
| `/tags` | POST | Authenticated users |
| `/files/<id>/tags` | POST | Authenticated owner |
| `/files/<id>/tags/<tag_id>/delete` | POST | Authenticated owner |
| `/api/uploads/start` | POST | Authenticated users (JSON + CSRF header) |
| `/api/uploads/<session_id>/status` | GET | Authenticated session owner |
| `/api/uploads/<session_id>/chunk/<n>` | PUT | Authenticated session owner (raw bytes + CSRF header) |
| `/api/uploads/<session_id>/complete` | POST | Authenticated session owner (CSRF header) |

## Resumable uploads

The My Files upload form uses the resumable API when JavaScript is available and
falls back to the normal `/files/upload` form otherwise.

- Chunk size is fixed at **2 MB**; the server calculates the exact final chunk.
- A session expires **24 hours** after creation. Expiry is derived from
  `upload_sessions.created_at`; no schema column was added.
- Chunks live under `temporary_uploads/<upload_id>/` and are never exposed
  directly. Upload IDs are random 32-character hex values.
- The browser can query `/api/uploads/<id>/status` to receive received/missing
  chunk numbers and resume after an interruption.
- The frontend stores the active session in `localStorage`. After a reload,
  select the same file and click **Resume upload** (the browser must provide the
  file data again); the server already holds completed chunks.
- `POST /api/uploads/<id>/complete` revalidates extension, size and quota,
  assembles chunks in order, hashes the result with SHA-256 in 64 KB reads and
  atomically moves the file into `storage/<user_id>/`.
- Repeated chunk requests overwrite the chunk atomically and are safe.
- Completed uploads are recorded as normal `files` rows; the session is marked
  `completed` and temporary chunks are removed.

CSRF tokens are sent in the `X-CSRF-Token` header for JSON and raw chunk
requests, while ordinary HTML forms continue to use the hidden field.

### Expired upload cleanup

Run the idempotent CLI command to remove expired temporary chunks, mark stale
in-progress sessions `aborted`, and leave permanent files untouched:

```powershell
flask --app app cleanup-uploads
```

Only sessions older than the 24-hour TTL are touched. The command can be run
with no active uploads and repeatedly without side effects.

## Security measures

- Passwords are hashed with Werkzeug's password-hashing utilities (current
  default: scrypt). Plaintext passwords are never stored or logged.
- All state-changing requests require a synchroniser CSRF token stored in the
  signed session; invalid or missing tokens return HTTP 400. HTML forms submit
  it as a hidden field; upload API calls send `X-CSRF-Token`.
- Authentication and role checks run on the server. `login_required` and
  `admin_required` decorators protect routes.
- Public registration ignores any submitted role; accounts are always `user`.
- Login uses username or email, verifies the hash and rejects inactive accounts.
- Login attempts (success and failure) are recorded in `login_attempts`; the
  password is never recorded.
- Login messages are generic to avoid revealing whether an account exists.
- The session is cleared before a new login and on logout.
- Templates never receive password hashes.
- File routes check ownership on the server; another user's file or folder
  returns 404 rather than revealing its existence.
- Uploaded files are stored under `storage/<user_id>/` using random UUID names;
  the original name is kept only as display metadata and is sanitised.
- Allowed upload extensions are checked on the server and configured through
  `CLOUDVAULT_ALLOWED_EXTENSIONS`; `MAX_CONTENT_LENGTH` limits request size.
- SHA-256 checksums are computed while streaming uploads in 64 KB chunks.
- Uploads are written to `temporary_uploads/` first and atomically moved into
  place, so an interrupted write never appears as a complete file.
- The per-user quota is enforced before a file is accepted and rechecked at
  finalization; `used_bytes` is recalculated from the `files` table after
  uploads and soft deletions.
- Upload-session routes require authentication and match `user_id` as well as
  the random upload ID, so another user's session returns 404.
- Chunk numbers, indexes and sizes are validated on the server; temporary paths
  are built from validated IDs and rejected if they escape
  `temporary_uploads/`.
- Chunks are written to unique temporary files and atomically renamed only after
  the expected byte count has been written. Finalization checks every chunk,
  rechecks extension/size/quota and removes the new permanent file if the
  database transaction fails.
- No JSON response or API error contains a server filesystem path.

## Quota configuration

New accounts receive `DEFAULT_USER_QUOTA_BYTES` (100 MiB by default), which can
be changed with `CLOUDVAULT_DEFAULT_QUOTA_BYTES`. Existing accounts with a stored
quota of `0` fall back to this default in the current UI and upload check.

## Project layout

```text
CloudVault/
├── app.py                 Development entry point
├── config.py              Environment-driven configuration
├── requirements.txt       Flask + pytest
├── schema.sql             Approved 11-table SQLite schema
├── app/
│   ├── __init__.py        Flask factory, CLI commands, app hooks
│   ├── auth.py            Registration, login, roles, helpers
│   ├── csrf.py            Minimal CSRF protection
│   ├── database.py        Connections, initialisation, verification
│   ├── files.py           File, folder and tag management
│   └── uploads.py         Resumable upload API and expired-session cleanup
├── instance/              SQLite database (runtime, ignored)
├── storage/               Current files (runtime, ignored)
├── backup/                Versioned backup copies (runtime, ignored)
├── temporary_uploads/     Incomplete uploads and chunks (runtime, ignored)
├── static/css/app.css     Plain CSS
├── static/js/app.js       Minimal vanilla JavaScript (password, view, Data Saver)
├── static/js/uploads.js   Chunked upload, progress, resume and retry UI
├── templates/             Jinja2 templates (auth, files, detail, dashboard, admin)
├── tests/                 Database and authentication tests (Stage 1–2)
├── PROJECT_DEVELOPMENT_PROTOCOL.md
└── REQUIREMENTS_TRACEABILITY_MATRIX.md
```

## Database

The database lives at `instance/cloudvault.db`. The approved schema contains
exactly these 11 application tables:

`users`, `folders`, `files`, `file_versions`, `tags`, `file_tags`, `shares`,
`upload_sessions`, `backup_checks`, `activity_logs`, `login_attempts`.

### Schema decisions and assumptions

- **Timestamps** are stored as ISO-8601 UTC text using
  `strftime('%Y-%m-%dT%H:%M:%fZ', 'now')`.
- **Foreign keys** are enabled on every connection (`PRAGMA foreign_keys = ON`).
- **Cross-user ownership** is enforced with composite foreign keys, so a record
  cannot reference another user's folder, file or tag.
- **Passwords** are stored only as `password_hash`.
- **Folders** support self-referencing parent/child relationships with partial
  unique indexes for name uniqueness.
- **Files** use `status` (`active`/`deleted`) plus `deleted_at` for the recycle
  bin.
- **Versions** are unique per `(file_id, version_number)` with SHA-256 checksums.
- **`users.used_bytes`** is a cached aggregate for quota checks; later stages
  must maintain it transactionally.
- **`upload_sessions`** tracks chunk progress for resumable uploads; incomplete
  uploads never appear as files until `complete` succeeds. Chunk size is the
  fixed 2 MB protocol constant and expiry is derived as `created_at + 24 hours`,
  so the approved schema was not altered.
- **`activity_logs`** and **`login_attempts`** are append-only audit records.

### Idempotent, transaction-safe initialisation

`init_db()` applies `schema.sql` atomically. It uses `IF NOT EXISTS` statements,
so repeated runs are safe. A schema error rolls back and leaves the database
unchanged. No seed users or sample business data are inserted.

## Storage warning

`backup/` is on the same server as primary storage. This protects against
accidental file loss but **not** against total server or disk failure. Off-site
backup is out of scope for this project.

## Tests

```powershell
python -m pytest -q
```

Tests use isolated temporary databases and explicit test secrets; the
development database is never used.

Current result: **66 passed** (Stage 1/1.1 database tests plus Stage 2
authentication, access-control, CSRF and administrator CLI tests). Stage 3 and
Stage 4 tests are deferred by the stage instructions. Stage 4 was checked with
Python compilation, template parsing, route inspection, JavaScript syntax
validation and an isolated temporary-database implementation smoke test; this
is not formal pytest evidence.

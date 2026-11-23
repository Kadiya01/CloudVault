# Requirements Traceability Matrix — Initial Baseline

This matrix is based on the academic report and the approved project decisions recorded in `PROJECT_DEVELOPMENT_PROTOCOL.md`. It must be updated whenever implementation and testing provide actual evidence.

**Stage 1 update (2026-11-22):** Setup and Database Schema is complete. The Flask application skeleton and the 11-table SQLite schema exist and were verified by executed tests.

**Stage 1.1 corrective update (2026-11-22):** Database integrity was strengthened with composite foreign keys that prevent cross-user relationships, schema initialisation was made transaction-safe, and ownership-integrity plus rollback tests were added.

**Stage 2 update (2026-11-22):** Registration, login, logout, password hashing, session security, CSRF protection and role-based access control were implemented and verified with automated tests. FR1 and NFR1 are now **Verified**. Later-stage functional requirements remain **Not started**; NFR2 and NFR5 remain **In progress**.

**Stage 3 update (2026-11-23):** File and folder management, upload/download, quota enforcement, tags, search and soft deletion were implemented. FR2, FR3, FR8 and FR13 move to **Implemented, unverified** because the stage instructions defer automated testing. No schema changes were made. Resumable uploads, versioned backups, recycle-bin restore and sharing remain **Not started**.

**Stage 4 update (2026-11-23):** Resumable 2 MB chunked uploads, session status/resume, progress UI, retry and expired-session cleanup were implemented using the existing `upload_sessions` table. FR12 moves to **Implemented, unverified**; NFR3 and NFR4 also move to **Implemented, unverified**. No schema changes were made. Versioned backups, recycle-bin restore and sharing remain **Not started**.

## Status Values

- Not started
- In progress
- Implemented, unverified
- Verified
- Blocked

## Functional Requirements

| ID | Requirement Description | Source | Development Stage | Planned Implementation Component | Verification Method | Current Implementation Status | Test Evidence | Outstanding Issues |
|---|---|---|---|---|---|---|---|---|
| FR1 | A user can register, log in and log out. | Academic report | 2 | `users`, `login_attempts` tables; auth routes; Flask session | Manual + automated auth tests | Verified | `tests/test_auth.py` registration, login (username/email), logout, inactive account, generic errors and login-attempt recording; 66-test suite passed 2026-11-22. Manual smoke: register 302→/login, login 302→/dashboard, logout 302→/login. | No rate limiting/account lockout beyond attempt logging; no password reset or email verification (out of scope for this stage). |
| FR2 | A user can upload files and download their own files. | Academic report | 3 | `files` table; `storage/`; upload/download routes | Functional upload/download tests with ownership checks | Implemented, unverified | `app/files.py` upload/download with ownership checks, extension and size validation, 64 KB SHA-256 streaming and UUID storage names; Stage 4 adds chunked upload finalization in `app/uploads.py`; templates `files.html` and `file_detail.html`. Stage 3/4 automated tests deferred; static checks and an isolated implementation smoke were run. | No formal executed test evidence; download and quota behaviour not yet covered by pytest; no download activity logging yet (FR10). |
| FR3 | A user can create folders, rename and move files. | Academic report | 3 | `folders` table; file/folder routes | Folder and file operation tests | Implemented, unverified | `app/files.py` folder create/rename/move (including cycle prevention) and file rename/move; responsive UI in `files.html`. Stage 3 automated tests deferred. | Cross-user and cycle cases not yet covered by executed tests. |
| FR4 | The system creates a new backup version on every upload or replacement. | Academic report | 5 | `file_versions` table; `backup/` | Version-creation tests on upload and replace | Not started | None | None recorded |
| FR5 | A user can view version history and restore any version. | Academic report | 5 | `file_versions` table; history and restore routes | Version history and restore tests | Not started | None | None recorded |
| FR6 | Deleted files go to a recycle bin and can be restored within 30 days. | Academic report | 6 | `files` status fields; recycle bin routes | Recycle bin and 30-day expiry tests | Not started | None | None recorded |
| FR7 | A user can share a file by a secret link with an expiry date. | Academic report | 6 | `shares` table; share routes | Share link generation and expiry tests | Not started | None | None recorded |
| FR8 | The system enforces a per-user storage quota. | Academic report | 3 | `users` quota field; upload validation | Quota enforcement tests | Implemented, unverified | Upload rejects files that exceed `users.quota_bytes`; `used_bytes` recalculated from `files` after upload and soft delete; new accounts receive `DEFAULT_USER_QUOTA_BYTES`; quota bar in UI. Implementation only; no executed quota tests. | Quota race between concurrent uploads not tested; administrators cannot change quotas until Stage 7. |
| FR9 | An administrator can add, disable or delete users and change quotas. | Academic report | 7 | `users` table; admin routes; role checks | Admin operation and role-check tests | Not started | None | None recorded |
| FR10 | The system logs uploads, downloads, deletions and logins. | Academic report | 7 | `activity_logs`, `login_attempts` tables | Activity logging tests | Not started | None | None recorded |
| FR11 | An administrator can verify backups using checksums and identify damaged copies. | Academic report | 5 and 7 | `backup_checks` table; checksum verification | Backup verification and corruption detection tests | Not started | None | None recorded |
| FR12 | Large files are uploaded in chunks and interrupted uploads can be resumed. | Academic report | 4 | `upload_sessions` table; `temporary_uploads/` | Chunked and resume upload tests | Implemented, unverified | `app/uploads.py` implements start/status/chunk/complete with fixed 2 MB chunks, 24-hour derived expiry, owner checks and missing-chunk reporting; `static/js/uploads.js` provides progress, resume and retry. Isolated temporary-database smoke covered start, repeated chunk, status, completion, checksum/quota and cleanup; no pytest run. | Formal tests deferred; after a full page reload the browser must reselect the same file to supply missing chunk bytes; concurrent completion of the same session is only guarded by the session `UPDATE ... WHERE status='in_progress'`. |
| FR13 | A user can add tags and search by filename or tag. | Academic report | 3 | `tags`, `file_tags` tables; search routes | Tagging and search tests | Implemented, unverified | Tag create/assign/remove routes; filename search with escaped `LIKE`; tag filter on `/files` and `/folders/<id>`; tags shown in list and detail views. Implementation only; no executed tests. | Tag name uniqueness is case-sensitive per the approved schema; no global tag management UI yet. |

## Non-Functional Requirements

| ID | Requirement Description | Source | Development Stage | Planned Implementation Component | Verification Method | Current Implementation Status | Test Evidence | Outstanding Issues |
|---|---|---|---|---|---|---|---|---|
| NFR1 | Security: hashed passwords and access control. | Academic report | 2 | Auth module; role checks; Flask session and CSRF protection | Security review and access-control tests | Verified | Werkzeug password hashing verified in tests; server-side `login_required`/`admin_required`; CSRF rejects missing/invalid tokens (400); HttpOnly + SameSite cookies; secret required from environment; unauth/normal/admin access tests passed 2026-11-22. | Secure cookie requires `CLOUDVAULT_COOKIE_SECURE=1` in HTTPS; no rate limiting, MFA or password reset yet. |
| NFR2 | Usability: a simple interface that works on phones. | Academic report | 3 | Jinja2 templates; plain CSS; `static/css/app.css` | Responsive/manual visual checks | In progress | Responsive auth, dashboard, admin, 403, My Files and file-detail templates implemented; My Files adds breadcrumbs, quota bar, compact view, Data Saver and a resumable-upload panel with progress/status/resume/retry; small-screen table becomes label/value rows. | Real-device/mobile visual verification not yet performed; later-stage UI pending. |
| NFR3 | Reliability: no file loss on failed uploads. | Academic report | 4 | `temporary_uploads/`; `upload_sessions` table | Interrupted upload recovery tests | Implemented, unverified | Stage 4 writes chunks to unique temporary files, atomically renames completed chunks, rebuilds the file in order, verifies sizes/checksum and removes the new permanent file if finalization fails. Status/resume and cleanup are implemented; isolated smoke covered repeated chunks and finalization. | Formal tests and concurrent-failure stress testing deferred; transient orphaned temp files are left to the idempotent cleanup command. |
| NFR4 | Performance: upload progress for large files. | Academic report | 4 | `static/js/uploads.js`; chunked upload flow | Progress reporting tests | Implemented, unverified | `static/js/uploads.js` shows percentage, progress bar, per-chunk status, automatic retry, explicit resume/retry and success/error messages; server status reports received/missing chunks. | Progress behaviour not verified in a real browser; no timing/throughput benchmark. |
| NFR5 | Portability: runs on Windows and Linux. | Academic report | 1 | Flask app; `pathlib`-based paths; schema initialisation | Cross-platform run verification | In progress | Full suite (66 tests) executed and passed on Windows 11 / Python 3.14.10, 2026-11-22. Linux not yet executed. | Linux run has not been verified. |

## Stage 1 Verification Evidence

| Check | Actual Result |
|---|---|
| Schema initialisation | `flask --app app init-db` reported all 11 application tables verified |
| Database path | `instance/cloudvault.db` |
| Application tables found | 11 |
| Table names | `activity_logs`, `backup_checks`, `file_tags`, `file_versions`, `files`, `folders`, `login_attempts`, `shares`, `tags`, `upload_sessions`, `users` |
| Legacy tables (`versions`, `activity_log`) | Absent |
| Foreign-key enforcement | Enabled (`PRAGMA foreign_keys = 1`) |
| Automated tests (initial Stage 1 run) | `python -m pytest -q` → 30 passed, 0 failed, 0 skipped |
| Test database isolation | Tests use temporary databases; development database untouched |

## Stage 1.1 Corrective Verification Evidence

| Check | Actual Result |
|---|---|
| Existing database before migration | 11 tables, **0 records** across all tables |
| Backup taken before schema change | `C:\Users\JAKADIYA\AppData\Local\Temp\opencode\cloudvault-db-backup-20261122-233308.db` |
| Migration approach | Atomic drop-and-recreate of empty tables inside one `BEGIN IMMEDIATE … COMMIT` transaction; aborted automatically if any rows existed |
| Application tables after migration | 11 |
| Legacy tables (`versions`, `activity_log`) | Absent |
| Foreign-key enforcement | Enabled (`PRAGMA foreign_keys = 1`) |
| Folder parent ownership | Composite FK `(parent_id, user_id) → folders(id, user_id)` |
| File folder ownership | Composite FK `(folder_id, user_id) → folders(id, user_id)` |
| File–tag ownership | `file_tags.user_id` with composite FKs to `files(id, user_id)` and `tags(id, user_id)` |
| Share ownership | Composite FK `(file_id, user_id) → files(id, user_id)` |
| Upload-session ownership | Composite FKs to `files(id, user_id)` and `folders(id, user_id)` |
| Transaction safety | `executescript` wrapped in explicit `BEGIN IMMEDIATE … COMMIT`; failure rolls back and leaves no partial schema |
| Automated tests (Stage 1.1) | `python -m pytest -q` → 43 passed, 0 failed, 0 skipped |
| Re-running initialisation | `flask --app app init-db` re-ran successfully; all 11 tables verified; existing data preserved (0 records) |

## Stage 2 Verification Evidence

| Check | Actual Result |
|---|---|
| Registration | Implemented; successful registration redirects to `/login`; role forced to `user`; duplicate username/email and invalid input rejected |
| Password storage | Werkzeug hash verified; plaintext password absent from stored hash and response body |
| Login | Username and email identifiers accepted; correct password creates session; wrong password, unknown account and inactive account rejected with a generic message |
| Logout | POST `/logout` clears the session; protected page redirects to login afterwards |
| Login attempts | `login_attempts` records successes and failures with `user_id` where known; no password stored |
| Session security | `SECRET_KEY` required from `CLOUDVAULT_SECRET_KEY`; missing key raises `RuntimeError`; HttpOnly and SameSite=Lax cookies; Secure configurable |
| CSRF | Missing or invalid token on login/register submissions returns HTTP 400 |
| Role protection | `/dashboard` redirects unauthenticated users; `/admin` returns 403 for normal users and 200 for administrators |
| Role elevation | Public registration with submitted `role=admin` still creates a `user` |
| Administrator provisioning | `flask --app app create-admin` created an admin with hashed password; duplicate rejected; password not printed |
| Application startup | `flask --app app routes` listed all expected routes with `CLOUDVAULT_SECRET_KEY` set |
| Manual smoke test | register 302→`/login`, login 302→`/dashboard`, dashboard 200, admin-as-user 403, logout 302→`/login`, post-login redirect to `/login`, login without CSRF 400 |
| Automated tests | `python -m pytest -q` → **66 passed**, 0 failed, 0 skipped |
| Development database | Unchanged: 11 tables, 0 users, 0 login attempts |

## Stage 3 Verification Evidence

| Check | Actual Result |
|---|---|
| Scope | File/folder create, rename, move, upload, download, tags, filename/tag search and soft deletion implemented; Stage 4–6 features excluded |
| Schema | No changes to `schema.sql`; all 11 approved tables unchanged |
| Ownership | Every file, folder and tag operation resolves records by `id` + `user_id`; missing or foreign records return 404 |
| Upload validation | Server-side extension allow-list and `MAX_CONTENT_LENGTH`; empty files rejected |
| Upload storage | Random UUID filename under `storage/<user_id>/`; original display name sanitised with `secure_filename` |
| Integrity | SHA-256 computed while streaming in 64 KB chunks and stored in `files.checksum_sha256` |
| Failure handling | Data written to `temporary_uploads/` first, atomically moved with `os.replace`; database rollback removes the new physical file |
| Quota | Upload rejected before acceptance when `used_bytes + size` exceeds quota; `used_bytes` recalculated from `files` after upload and soft delete |
| Folder safety | Moving a folder into itself or a descendant is rejected; same-user parent and target folders are required |
| Search safety | User input escaped for SQL `LIKE`; tag filter is constrained by `user_id` |
| Soft deletion | Delete sets `status='deleted'` and `deleted_at`; physical file retained for the Stage 6 recycle bin |
| UI | `/files` and `/folders/<id>` list folders/files, show breadcrumbs/quota, offer upload/new-folder/tag forms, search, compact view and Data Saver; file detail shows checksum and tags |
| Static checks | `python -m compileall` succeeded; all templates parsed; blueprint registered 14 file/folder/tag routes |
| Automated tests | **Not run** — Stage 3 instructions defer the test suite; no pass result is claimed |
| Development database | No Stage 3 migration or schema write was performed; development data was not modified by verification |

## Stage 4 Verification Evidence

| Check | Actual Result |
|---|---|
| Scope | Resumable chunked uploads only; Stage 5–6 features not added |
| Chunk size | Fixed at 2 MB (`2 * 1024 * 1024` bytes); final chunk size calculated by the server |
| Session expiry | Derived as `created_at + 24 hours`; no schema column added |
| Schema | No changes to `schema.sql`; `upload_sessions` reused with its existing columns/statuses |
| Endpoints | `POST /api/uploads/start`, `GET /api/uploads/<id>/status`, `PUT /api/uploads/<id>/chunk/<n>`, `POST /api/uploads/<id>/complete` |
| Authentication | Every API endpoint rejects unauthenticated callers with JSON 401; session lookups match both random upload ID and `user_id` |
| Ownership | Another user's session returns 404; status/chunk/complete do not reveal paths or foreign session details |
| CSRF | Header `X-CSRF-Token` accepted for JSON/raw requests; existing form-field flow preserved; missing token returns 400 |
| Start validation | Filename sanitised and extension checked; positive integer size; `MAX_CONTENT_LENGTH`; owner-scoped destination folder; quota pre-check |
| Chunk validation | Session active/not expired; index in range; exact expected byte count from HTTP Content-Length and streamed count; unique temp file then atomic rename |
| Repeated chunks | Overwrites the same chunk atomically; isolated smoke verified a repeated chunk does not duplicate progress |
| Assembly | Every chunk present and exact size; ordered assembly; SHA-256 with 64 KB reads; total size rechecked |
| Finalization | Extension/size/quota rechecked; UUID permanent name; atomic move into `storage/<user_id>/`; files row and completed session committed together |
| Failure handling | Database rollback and removal of the new permanent file on finalization failure; chunks remain available for retry |
| Quota | Rechecked inside the finalization transaction after the session write lock; `used_bytes` recalculated from `files`; session data is not counted twice |
| Cleanup | `flask --app app cleanup-uploads` removes expired temp directories, marks stale in-progress sessions `aborted`, and never touches permanent storage; idempotent |
| Frontend | Existing form retained as no-JS fallback; JavaScript uploads 2 MB chunks, shows percentage/status, resumes persisted sessions, retries failed chunks and disables duplicate submissions |
| Static checks | Python compilation, all templates parsed, route inspection and `node --check static/js/uploads.js` succeeded |
| Implementation smoke | Isolated temporary database/directories: start → 3 chunks (including repeated chunk) → status → complete → checksum/size/quota/temp-cleanup verified; also missing-chunk 409, missing-CSRF 400 and foreign-user 404 |
| Formal tests | **Not run** — pytest deferred by Stage 4 instructions; no pass result is claimed |
| Development database | Stage 4 implementation checks used temporary paths only; no live schema or data change |

## Stage 1 Foundation Map

The schema provides the structural foundation for later stages but does not implement their behaviour:

- FR1, FR8, FR9 → `users`, `login_attempts`
- FR2, FR3, FR10 → `files`, `folders`, `tags`, `file_tags`, `activity_logs`
- FR4, FR5, FR11 → `file_versions`, `backup_checks`
- FR6, FR7 → `files.status`/`deleted_at`, `shares`
- FR12 → `upload_sessions`
- NFR1–NFR4 → constraints and supporting columns only; feature logic not implemented

Cross-user ownership is now enforced at the schema level for folders, files, tags, shares and upload sessions. Server-side ownership checks in application code remain mandatory in all later stages; the database constraints are additional defence, not a replacement.

## Maintenance Note

This matrix must be updated as each development stage is implemented and tested. Test evidence must reference real executed results; outstanding issues must be recorded as they are discovered. Requirements remain **Not started** until their feature logic exists and has been verified.

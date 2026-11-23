# CloudVault — Project Development Protocol

## 1. Project Objective

Design and implement a cloud-based file storage and backup system using Python, Flask, SQLite, HTML, Jinja2, plain CSS and minimal vanilla JavaScript.

The system is intended to let users securely store, organise, version, share and restore files, with an administrative layer for user and backup management.

## 2. Fixed Architecture

| Area | Decision |
|---|---|
| Backend | Python + Flask |
| Database | SQLite |
| Frontend | HTML + Jinja2 |
| Styling | Plain CSS |
| JavaScript | Minimal vanilla JavaScript |
| Main stylesheet | `static/css/app.css` |
| Main JavaScript | `static/js/app.js` |
| Deployment | Single server |
| Backup destination | Local server storage only |

Do not introduce frontend frameworks, additional JavaScript libraries, paid services, external backup providers, cloud object storage or a replacement database without explicit approval.

## 3. Approved Database Tables

The implementation must contain exactly these 11 tables:

1. `users`
2. `folders`
3. `files`
4. `file_versions`
5. `tags`
6. `file_tags`
7. `shares`
8. `upload_sessions`
9. `backup_checks`
10. `activity_logs`
11. `login_attempts`

Do not create duplicate legacy tables named `versions` or `activity_log`.

## 4. Approved Storage Directories

| Directory | Purpose |
|---|---|
| `storage/` | Current files |
| `backup/` | Versioned backup copies |
| `temporary_uploads/` | Incomplete uploads and chunks |

The backup directory is on the same server as primary storage. This does not protect against total server or disk failure. Off-server or off-site protection is out of scope for this project.

## 5. Development Stages

The following order is fixed and must be preserved:

1. Setup and Database Schema.
2. Authentication and Roles.
3. File and Folder Management.
4. Resumable Uploads.
5. Versioned Backups.
6. Recycle Bin and Sharing.
7. Administration.
8. Testing and Documentation.

Each stage must be implemented and tested separately. For every stage, report changed files, commands, actual results and unresolved issues. Stop and wait for owner approval before starting the next stage.

## 6. Collaboration Responsibilities

| Contributor | Responsibility |
|---|---|
| OpenCode | Project coordination, inspection, Git and verification |
| Codex | Complex backend engineering, security and technical review |
| GitHub Copilot | Coding assistance, completion and small refactoring |
| Google Antigravity | Frontend development and visual testing |

The project owner retains final approval authority.

## 7. Mandatory Engineering Rules

- Verify file ownership on every file operation.
- Hash passwords; never store plaintext passwords.
- Protect administrator operations with server-side role checks.
- Validate uploaded files and filenames.
- Prevent directory traversal.
- Use secure session handling and CSRF protection.
- Preserve old versions when replacing files.
- Restore versions by creating new history records.
- Do not expose incomplete uploads as completed files.
- Enforce storage quotas.
- Record important operations.
- Never claim tests passed unless they were actually executed.

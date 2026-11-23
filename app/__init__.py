"""CloudVault Flask application factory.

Stage 2 adds authentication, sessions and role-based access control on top of
the Stage 1 database foundation. Stage 3 adds ownership-checked file, folder
and tag management. Stage 4 adds resumable 2 MB chunked uploads. Stage 5 adds verified,
versioned backups and restoration. Sharing belongs to a later stage.
"""

import os
import sqlite3
from pathlib import Path

import click
from flask import Flask, g, redirect, render_template, url_for

from . import auth, database, files, uploads, versions
from .csrf import generate_csrf_token, protect_state_changing_requests

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def create_app(test_config=None):
    """Create and configure the Flask application."""
    app = Flask(
        __name__,
        instance_relative_config=False,
        template_folder=str(PROJECT_ROOT / "templates"),
        static_folder=str(PROJECT_ROOT / "static"),
    )
    app.config.from_object("config.Config")

    # Read the secret at factory time so the environment is respected, then
    # allow tests to provide an explicit value.
    app.config["SECRET_KEY"] = os.environ.get("CLOUDVAULT_SECRET_KEY")
    if test_config:
        app.config.update(test_config)

    if not app.config.get("SECRET_KEY"):
        raise RuntimeError(
            "CLOUDVAULT_SECRET_KEY is not set. Set this environment variable "
            "before starting CloudVault; no insecure fallback is provided."
        )

    Path(app.config["INSTANCE_DIR"]).mkdir(parents=True, exist_ok=True)
    database.ensure_storage_dirs(app.config["BASE_DIR"])

    app.before_request(protect_state_changing_requests)
    app.teardown_appcontext(database.close_db)
    app.register_blueprint(auth.bp)
    app.register_blueprint(files.bp)
    app.register_blueprint(uploads.bp)
    app.register_blueprint(versions.bp)

    @app.context_processor
    def inject_auth_context():
        return {
            "current_user": g.get("user"),
            "csrf_token": generate_csrf_token(),
        }

    @app.route("/")
    def index():
        if g.get("user") is not None:
            return redirect(url_for("auth.dashboard"))
        return redirect(url_for("auth.login"))

    @app.errorhandler(403)
    def forbidden(error):
        return render_template("403.html"), 403

    @app.errorhandler(413)
    def request_too_large(error):
        if g.get("user") is not None:
            return render_template(
                "error.html",
                code=413,
                title="Upload too large",
                message=(
                    "The upload exceeded the maximum allowed request size. "
                    "Choose a smaller file and try again."
                ),
            ), 413
        return render_template(
            "error.html",
            code=413,
            title="Request too large",
            message="The request exceeded the maximum allowed size.",
        ), 413

    @app.cli.command("init-db")
    def init_db_command():
        """Initialise the CloudVault database schema."""
        database.init_db(app.config["DATABASE"], app.config["SCHEMA_PATH"])
        connection = database.connect(app.config["DATABASE"])
        try:
            result = database.verify_schema(connection)
        finally:
            connection.close()
        if result["ok"]:
            print("Database initialised; all 11 application tables verified.")
        else:
            print(f"Schema verification failed: {result}")
            raise SystemExit(1)

    @app.cli.command("cleanup-uploads")
    def cleanup_uploads_command():
        """Remove expired resumable-upload chunks and abort stale sessions."""
        db_path = Path(app.config["DATABASE"])
        if not db_path.is_file():
            click.echo("Database not found; no upload sessions to clean up.")
            return
        connection = database.connect(app.config["DATABASE"])
        try:
            stats = uploads.cleanup_expired_uploads(
                connection, app.config["TEMPORARY_UPLOADS_DIR"]
            )
        except sqlite3.Error as exc:
            click.echo(f"Upload cleanup failed: {exc}", err=True)
            raise SystemExit(1)
        finally:
            connection.close()
        click.echo(
            "Upload cleanup complete: "
            f"{stats['sessions_scanned']} expired session(s) scanned, "
            f"{stats['directories_removed']} temporary directories removed, "
            f"{stats['sessions_aborted']} session(s) marked aborted, "
            f"{stats['errors']} error(s)."
        )

    @app.cli.command("create-admin")
    @click.option("--username", prompt="Username")
    @click.option("--email", prompt="Email")
    @click.option(
        "--password",
        prompt="Password",
        hide_input=True,
        confirmation_prompt=True,
    )
    def create_admin_command(username, email, password):
        """Create an administrator account from trusted CLI input."""
        database.init_db(app.config["DATABASE"], app.config["SCHEMA_PATH"])
        connection = database.connect(app.config["DATABASE"])
        try:
            username = username.strip()
            email = email.strip().lower()
            errors = auth.validate_registration(
                username, email, password, password
            )
            if errors:
                for error in errors:
                    click.echo(f"Error: {error}", err=True)
                raise SystemExit(1)
            if auth.user_exists(connection, username, email):
                click.echo(
                    "Error: username or email address already exists.", err=True
                )
                raise SystemExit(1)
            auth.create_user(connection, username, email, password, role="admin")
            click.echo(f"Administrator '{username}' created.")
        finally:
            connection.close()

    return app

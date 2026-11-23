"""Authentication, sessions and role-based access control (Stage 2).

Passwords are hashed with Werkzeug's utilities. Plaintext passwords are never
stored, logged or exposed to templates.
"""

import re
import sqlite3
from functools import wraps

from flask import (
    Blueprint,
    abort,
    current_app,
    flash,
    g,
    has_app_context,
    redirect,
    render_template,
    request,
    session,
    url_for,
)
from werkzeug.security import check_password_hash, generate_password_hash

from . import database

bp = Blueprint("auth", __name__)

USERNAME_RE = re.compile(r"^[A-Za-z0-9_.-]{3,50}$")
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
MIN_PASSWORD_LENGTH = 8
MAX_PASSWORD_LENGTH = 128

# Used only to equalise password-check timing for unknown accounts, reducing
# user enumeration through response timing.
_DUMMY_HASH = generate_password_hash("cloudvault-dummy-password-not-a-real-user")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def validate_registration(username, email, password, confirm_password):
    """Return a list of human-readable validation errors (empty when valid)."""
    errors = []
    username = (username or "").strip()
    email = (email or "").strip().lower()
    password = password or ""
    confirm_password = confirm_password or ""

    if not USERNAME_RE.fullmatch(username):
        errors.append(
            "Username must be 3-50 characters using letters, numbers, dots, "
            "dashes or underscores."
        )
    if not EMAIL_RE.fullmatch(email):
        errors.append("Enter a valid email address.")
    if len(password) < MIN_PASSWORD_LENGTH:
        errors.append(
            f"Password must be at least {MIN_PASSWORD_LENGTH} characters long."
        )
    elif len(password) > MAX_PASSWORD_LENGTH:
        errors.append("Password is too long.")
    if password != confirm_password:
        errors.append("Passwords do not match.")
    return errors


def find_user_by_identifier(connection, identifier):
    """Find a user by case-insensitive username or email."""
    return connection.execute(
        "SELECT id, username, email, password_hash, role, is_active "
        "FROM users WHERE lower(username) = lower(?) OR lower(email) = lower(?)",
        (identifier, identifier),
    ).fetchone()


def user_exists(connection, username, email):
    """Return True when the username or email (case-insensitive) is taken."""
    row = connection.execute(
        "SELECT 1 FROM users "
        "WHERE lower(username) = lower(?) OR lower(email) = lower(?) LIMIT 1",
        (username, email),
    ).fetchone()
    return row is not None


def create_user(connection, username, email, password, role="user",
                quota_bytes=None):
    """Insert a user with a hashed password and return the new id.

    New accounts receive the configured default quota unless an explicit value
    is supplied.
    """
    if quota_bytes is None:
        quota_bytes = (
            int(current_app.config.get("DEFAULT_USER_QUOTA_BYTES", 0))
            if has_app_context()
            else 0
        )
    password_hash = generate_password_hash(password)
    cursor = connection.execute(
        "INSERT INTO users (username, email, password_hash, role, quota_bytes) "
        "VALUES (?, ?, ?, ?, ?)",
        (username.strip(), email.strip().lower(), password_hash, role, quota_bytes),
    )
    connection.commit()
    return cursor.lastrowid


def record_login_attempt(connection, user_id, identifier, success):
    """Record a login attempt without ever storing the password."""
    identifier = (identifier or "").strip() or "unknown"
    user_agent = request.user_agent.string if request.user_agent else None
    connection.execute(
        "INSERT INTO login_attempts "
        "(user_id, username_attempted, success, ip_address, user_agent) "
        "VALUES (?, ?, ?, ?, ?)",
        (
            user_id,
            identifier[:255],
            1 if success else 0,
            request.remote_addr,
            user_agent[:512] if user_agent else None,
        ),
    )
    connection.commit()


# ---------------------------------------------------------------------------
# Request hooks, decorators and context
# ---------------------------------------------------------------------------

@bp.before_app_request
def load_logged_in_user():
    """Load the current user from the session on each request."""
    user_id = session.get("user_id")
    if user_id is None:
        g.user = None
        return

    user = database.get_db().execute(
        "SELECT id, username, email, role, quota_bytes, used_bytes, is_active "
        "FROM users WHERE id = ?",
        (user_id,),
    ).fetchone()

    if user is None or user["is_active"] != 1:
        session.clear()
        g.user = None
        return

    g.user = user


def login_required(view):
    """Redirect unauthenticated users to the login page."""

    @wraps(view)
    def wrapped_view(*args, **kwargs):
        if g.get("user") is None:
            flash("Please log in to continue.", "warning")
            return redirect(url_for("auth.login"))
        return view(*args, **kwargs)

    return wrapped_view


def admin_required(view):
    """Allow only authenticated administrators; reject everyone else."""

    @wraps(view)
    def wrapped_view(*args, **kwargs):
        if g.get("user") is None:
            flash("Please log in to continue.", "warning")
            return redirect(url_for("auth.login"))
        if g.user["role"] != "admin":
            abort(403)
        return view(*args, **kwargs)

    return wrapped_view


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@bp.route("/register", methods=("GET", "POST"))
def register():
    if g.get("user") is not None:
        return redirect(url_for("auth.dashboard"))

    if request.method == "POST":
        username = request.form.get("username", "").strip()
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        confirm_password = request.form.get("confirm_password", "")

        errors = validate_registration(username, email, password, confirm_password)
        connection = database.get_db()

        if not errors and user_exists(connection, username, email):
            errors.append("That username or email address is already registered.")

        if not errors:
            try:
                # Role is forced to 'user'; any submitted role is ignored.
                create_user(connection, username, email, password, role="user")
            except sqlite3.IntegrityError:
                errors.append(
                    "That username or email address is already registered."
                )

        if not errors:
            flash("Registration successful. Please log in.", "success")
            return redirect(url_for("auth.login"))

        for error in errors:
            flash(error, "error")

    return render_template("register.html")


@bp.route("/login", methods=("GET", "POST"))
def login():
    if g.get("user") is not None:
        return redirect(url_for("auth.dashboard"))

    if request.method == "POST":
        identifier = request.form.get("identifier", "").strip()
        password = request.form.get("password", "")
        connection = database.get_db()
        user = (
            find_user_by_identifier(connection, identifier)
            if identifier
            else None
        )

        success = False
        if user is None:
            # Equalise timing for unknown accounts.
            check_password_hash(_DUMMY_HASH, password)
        elif user["is_active"] == 1 and check_password_hash(
            user["password_hash"], password
        ):
            success = True

        record_login_attempt(
            connection,
            user["id"] if user is not None else None,
            identifier,
            success,
        )

        if success:
            session.clear()
            session["user_id"] = user["id"]
            session.permanent = True
            flash("You are logged in.", "success")
            return redirect(url_for("auth.dashboard"))

        # Generic message: do not reveal whether the account exists or whether
        # it is inactive.
        flash("Invalid username/email or password.", "error")

    return render_template("login.html")


@bp.route("/logout", methods=("POST",))
def logout():
    session.clear()
    flash("You have been logged out.", "success")
    return redirect(url_for("auth.login"))


@bp.route("/dashboard")
@login_required
def dashboard():
    return render_template("dashboard.html")


@bp.route("/admin")
@admin_required
def admin():
    connection = database.get_db()
    user_count = connection.execute("SELECT COUNT(*) FROM users").fetchone()[0]
    return render_template("admin.html", user_count=user_count)

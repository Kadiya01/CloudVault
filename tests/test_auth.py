"""Stage 2 tests: registration, login/logout, sessions, roles and CSRF."""

import pytest
from werkzeug.security import check_password_hash, generate_password_hash

from app import create_app, database

PASSWORD = "Password123!"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def csrf_token(client, path="/login"):
    response = client.get(path)
    assert response.status_code == 200
    with client.session_transaction() as session:
        return session["_csrf_token"]


def add_user(app, username="alice", email="alice@example.com",
             password=PASSWORD, role="user", is_active=1):
    conn = database.connect(app.config["DATABASE"])
    try:
        cursor = conn.execute(
            "INSERT INTO users (username, email, password_hash, role, is_active) "
            "VALUES (?, ?, ?, ?, ?)",
            (
                username,
                email,
                # A fast hash keeps the suite quick; verification is agnostic
                # to the hashing method. The application itself uses Werkzeug's
                # current default (scrypt).
                generate_password_hash(password, method="pbkdf2:sha256:1000"),
                role,
                is_active,
            ),
        )
        conn.commit()
        return cursor.lastrowid
    finally:
        conn.close()


def fetch_user(app, username):
    conn = database.connect(app.config["DATABASE"])
    try:
        return conn.execute(
            "SELECT * FROM users WHERE username = ?", (username,)
        ).fetchone()
    finally:
        conn.close()


def login_attempts(app):
    conn = database.connect(app.config["DATABASE"])
    try:
        return conn.execute("SELECT * FROM login_attempts ORDER BY id").fetchall()
    finally:
        conn.close()


def register(client, username="alice", email="alice@example.com",
             password=PASSWORD, confirm=None, extra=None):
    data = {
        "csrf_token": csrf_token(client, "/register"),
        "username": username,
        "email": email,
        "password": password,
        "confirm_password": password if confirm is None else confirm,
    }
    if extra:
        data.update(extra)
    return client.post("/register", data=data)


def login(client, identifier="alice", password=PASSWORD):
    return client.post(
        "/login",
        data={
            "csrf_token": csrf_token(client, "/login"),
            "identifier": identifier,
            "password": password,
        },
    )


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------

def test_registration_success(client, app):
    response = register(client)
    assert response.status_code == 302
    assert "/login" in response.headers["Location"]

    user = fetch_user(app, "alice")
    assert user is not None
    assert user["role"] == "user"
    assert user["email"] == "alice@example.com"
    assert user["password_hash"] != PASSWORD
    assert check_password_hash(user["password_hash"], PASSWORD)
    assert PASSWORD.encode() not in response.data


def test_registration_rejects_duplicate_username(client, app):
    add_user(app, username="alice", email="alice@example.com")
    response = register(client, username="alice", email="other@example.com")
    assert response.status_code == 200
    assert b"already registered" in response.data

    conn = database.connect(app.config["DATABASE"])
    try:
        count = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
    finally:
        conn.close()
    assert count == 1


def test_registration_rejects_duplicate_email(client, app):
    add_user(app, username="alice", email="alice@example.com")
    response = register(client, username="bob", email="alice@example.com")
    assert response.status_code == 200
    assert b"already registered" in response.data


def test_registration_rejects_invalid_input(client, app):
    response = register(
        client, username="a", email="not-an-email", password="short", confirm="other"
    )
    assert response.status_code == 200
    assert b"Username must be" in response.data
    assert b"valid email" in response.data
    assert b"Password must be" in response.data
    assert fetch_user(app, "a") is None


def test_registration_rejects_password_mismatch(client, app):
    response = register(client, confirm="DifferentPassword123!")
    assert response.status_code == 200
    assert b"Passwords do not match" in response.data
    assert fetch_user(app, "alice") is None


def test_public_registration_cannot_create_admin(client, app):
    response = register(client, extra={"role": "admin"})
    assert response.status_code == 302
    user = fetch_user(app, "alice")
    assert user["role"] == "user"


# ---------------------------------------------------------------------------
# Login and logout
# ---------------------------------------------------------------------------

def test_login_success_by_username(client, app):
    user_id = add_user(app)
    response = login(client)
    assert response.status_code == 302
    assert "/dashboard" in response.headers["Location"]

    with client.session_transaction() as session:
        assert session["user_id"] == user_id

    attempts = login_attempts(app)
    assert len(attempts) == 1
    assert attempts[0]["success"] == 1
    assert attempts[0]["user_id"] == user_id


def test_login_success_by_email(client, app):
    add_user(app, email="alice@example.com")
    response = login(client, identifier="alice@example.com")
    assert response.status_code == 302
    assert "/dashboard" in response.headers["Location"]


def test_login_wrong_password(client, app):
    add_user(app)
    response = login(client, password="WrongPassword123!")
    assert response.status_code == 200
    assert b"Invalid username/email or password" in response.data

    with client.session_transaction() as session:
        assert "user_id" not in session

    attempts = login_attempts(app)
    assert len(attempts) == 1
    assert attempts[0]["success"] == 0


def test_login_unknown_account(client, app):
    response = login(client, identifier="nobody")
    assert response.status_code == 200
    assert b"Invalid username/email or password" in response.data

    with client.session_transaction() as session:
        assert "user_id" not in session

    attempts = login_attempts(app)
    assert len(attempts) == 1
    assert attempts[0]["user_id"] is None
    assert attempts[0]["success"] == 0


def test_login_inactive_account_rejected(client, app):
    add_user(app, is_active=0)
    response = login(client)
    assert response.status_code == 200
    assert b"Invalid username/email or password" in response.data

    with client.session_transaction() as session:
        assert "user_id" not in session

    attempts = login_attempts(app)
    assert attempts[0]["success"] == 0


def test_logout_clears_session(client, app):
    add_user(app)
    login(client)

    response = client.post(
        "/logout", data={"csrf_token": csrf_token(client, "/dashboard")}
    )
    assert response.status_code == 302
    assert "/login" in response.headers["Location"]

    with client.session_transaction() as session:
        assert "user_id" not in session

    assert client.get("/dashboard").status_code == 302


def test_login_attempts_recorded(client, app):
    add_user(app)
    login(client, password="WrongPassword123!")
    login(client, password=PASSWORD)
    attempts = login_attempts(app)
    assert [a["success"] for a in attempts] == [0, 1]


# ---------------------------------------------------------------------------
# Access control
# ---------------------------------------------------------------------------

def test_protected_page_requires_login(client):
    response = client.get("/dashboard")
    assert response.status_code == 302
    assert "/login" in response.headers["Location"]


def test_normal_user_cannot_access_admin(client, app):
    add_user(app, role="user")
    login(client)
    response = client.get("/admin")
    assert response.status_code == 403


def test_admin_can_access_admin(client, app):
    add_user(app, username="root", email="root@example.com", role="admin")
    login(client, identifier="root")
    response = client.get("/admin")
    assert response.status_code == 200
    assert b"Administrator area" in response.data


def test_user_cannot_elevate_role_via_profile_data(client, app):
    register(client, extra={"role": "admin", "is_admin": "1"})
    user = fetch_user(app, "alice")
    assert user["role"] == "user"


# ---------------------------------------------------------------------------
# Security: CSRF and secret configuration
# ---------------------------------------------------------------------------

def test_login_rejects_missing_csrf_token(client):
    response = client.post(
        "/login", data={"identifier": "alice", "password": PASSWORD}
    )
    assert response.status_code == 400


def test_login_rejects_invalid_csrf_token(client):
    client.get("/login")
    response = client.post(
        "/login",
        data={
            "csrf_token": "invalid-token",
            "identifier": "alice",
            "password": PASSWORD,
        },
    )
    assert response.status_code == 400


def test_register_rejects_missing_csrf_token(client):
    response = client.post(
        "/register",
        data={
            "username": "alice",
            "email": "alice@example.com",
            "password": PASSWORD,
            "confirm_password": PASSWORD,
        },
    )
    assert response.status_code == 400


def test_missing_secret_key_fails_clearly(monkeypatch, tmp_path):
    monkeypatch.delenv("CLOUDVAULT_SECRET_KEY", raising=False)
    with pytest.raises(RuntimeError):
        create_app(
            {
                "TESTING": True,
                "DATABASE": str(tmp_path / "x.db"),
                "BASE_DIR": tmp_path,
                "INSTANCE_DIR": tmp_path / "instance",
            }
        )


# ---------------------------------------------------------------------------
# Administrator provisioning
# ---------------------------------------------------------------------------

def test_create_admin_cli(app):
    runner = app.test_cli_runner()
    result = runner.invoke(
        args=["create-admin"],
        input="root\nroot@example.com\nAdminPass123!\nAdminPass123!\n",
    )
    assert result.exit_code == 0, result.output
    assert "AdminPass123!" not in result.output

    user = fetch_user(app, "root")
    assert user is not None
    assert user["role"] == "admin"
    assert check_password_hash(user["password_hash"], "AdminPass123!")


def test_create_admin_cli_rejects_duplicate(app):
    add_user(app, username="root", email="root@example.com", role="admin")
    runner = app.test_cli_runner()
    result = runner.invoke(
        args=["create-admin"],
        input="root\nroot@example.com\nAdminPass123!\nAdminPass123!\n",
    )
    assert result.exit_code != 0
    assert "already exists" in result.output

from urllib.parse import parse_qs, urlparse

import pytest

from app import create_app
from db import get_db


@pytest.fixture
def app(tmp_path):
    return create_app({
        "TESTING": True,
        "SECRET_KEY": "local-test-secret",
        "DATABASE": str(tmp_path / "reports.sqlite3"),
        "UPLOAD_FOLDER": str(tmp_path / "photos"),
        "GOOGLE_CLIENT_ID": "example.apps.googleusercontent.com",
        "GOOGLE_CLIENT_SECRET": "example-secret",
        "GOOGLE_REDIRECT_URI": "http://localhost:5000/auth/google/callback",
        "ALLOWED_GOOGLE_EMAILS": {"student@example.test"},
    })


def start(client):
    response = client.get("/auth/google/start")
    assert response.status_code == 302
    return parse_qs(urlparse(response.location).query)


def test_sign_in_requests_identity_scopes_and_pkce(app):
    query = start(app.test_client())
    assert set(query["scope"][0].split()) == {"openid", "https://www.googleapis.com/auth/userinfo.email"}
    assert query["access_type"] == ["online"]
    assert query["code_challenge_method"] == ["S256"]
    assert query["state"][0] and query["nonce"][0]


def test_sign_in_requires_configuration(app):
    app.config["GOOGLE_CLIENT_ID"] = None
    assert app.test_client().get("/auth/google/start").status_code == 503


def test_callback_rejects_wrong_state(app):
    client = app.test_client()
    start(client)
    response = client.get("/auth/google/callback", query_string={"state": "wrong", "code": "demo"})
    assert response.status_code == 400
    with client.session_transaction() as session:
        assert "user_id" not in session


def test_overlapping_attempts_do_not_replace_each_other_and_cannot_move_browsers(app, monkeypatch):
    from routes import auth

    browser = app.test_client()
    first = start(browser)
    later = start(browser)
    callbacks = []

    def exchange(code, pending):
        callbacks.append(pending["state"])
        query = first if code == "first" else later
        return {"nonce": query["nonce"][0], "sub": "student", "email": "student@example.test",
                "email_verified": True}

    monkeypatch.setattr(auth, "_exchange_identity", exchange)
    path = "/auth/google/callback"
    first_params = {"state": first["state"][0], "code": "first"}
    wrong_browser = app.test_client().get(path, query_string=first_params)
    assert wrong_browser.status_code == 400
    assert "Try Google sign-in again" in wrong_browser.text
    assert browser.get(path, query_string=first_params).status_code == 303
    assert browser.get(path, query_string={"state": later["state"][0], "code": "later"}).status_code == 303
    assert browser.get(path, query_string=first_params).status_code == 400
    assert callbacks == [first["state"][0], later["state"][0]]


def test_expired_browser_attempt_returns_retry_page(app, monkeypatch):
    from routes import auth

    app.config["SESSION_COOKIE_SECURE"] = True
    browser = app.test_client()
    now = 1_000
    monkeypatch.setattr(auth.time, "time", lambda: now)
    response = browser.get("/auth/google/start", base_url="https://preview.example.test")
    state = parse_qs(urlparse(response.location).query)["state"][0]
    cookie = response.headers["Set-Cookie"]
    assert all(value in cookie for value in ("Secure", "HttpOnly", "SameSite=Lax", "Path=/auth/google/callback"))
    now += 601
    response = browser.get("/auth/google/callback", base_url="https://preview.example.test",
                           query_string={"state": state, "code": "expired"})
    assert response.status_code == 400
    assert "Try Google sign-in again" in response.text


@pytest.mark.parametrize("mode", ["allowed", "wrong_nonce", "not_allowlisted", "unverified"])
def test_callback_validates_identity_before_starting_session(app, monkeypatch, mode):
    from routes import auth

    client = app.test_client()
    query = start(client)
    claims = {
        "nonce": "wrong" if mode == "wrong_nonce" else query["nonce"][0],
        "email": "other@example.test" if mode == "not_allowlisted" else "student@example.test",
        "email_verified": mode != "unverified",
        "sub": "student-subject",
    }
    monkeypatch.setattr(auth, "_exchange_identity", lambda code, pending: claims)
    address = "/auth/google/callback"
    parameters = {"state": query["state"][0], "code": "demo"}
    response = client.get(address, query_string=parameters)
    with client.session_transaction() as session:
        user_id = session.get("user_id")
        assert "oauth_login" not in session
    with app.app_context():
        count = get_db().execute("SELECT COUNT(*) FROM users").fetchone()[0]
    if mode == "allowed":
        assert response.status_code == 303
        assert user_id and count == 1
        token = client.get("/api/csrf").json["csrf_token"]
        assert client.post("/logout", headers={"X-CSRFToken": token}).status_code == 303
        with client.session_transaction() as session:
            assert "user_id" not in session
    else:
        assert response.status_code == 403
        assert user_id is None and count == 0
    assert client.get(address, query_string=parameters).status_code == 400
